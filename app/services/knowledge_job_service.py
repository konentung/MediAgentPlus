"""知識庫維護工作（後台「系統資料匯入」）：衛教 JSONL 匯入、向量計算。

兩件事都要跑幾分鐘，不能在一個 HTTP 請求內做完，所以：
1. API 只負責收檔、建一列 `knowledge_jobs`、交給 `spawn` 在背景執行緒跑 `run()`；
2. 執行中定期把計數寫回 `progress`，頁面輪詢 `GET /api/v1/admin/knowledge/jobs` 顯示；
3. 同一時間只允許一個工作（避免同時匯入 + 算向量互相干擾）；
   執行程序若中途被重啟，超過 `STALE_MINUTES` 沒更新的工作視為中斷，讓下一個工作能開始。
只有 SUPER_ADMIN 能操作（與 CLI 的 `flask knowledge import-health-education` / `embed` 相同）。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from app.core.exceptions import AppError, AuthorizationError, ConflictError, ValidationError
from app.domain.enums import Role
from app.domain.principal import Principal
from app.repositories.knowledge_repository import KnowledgeJobRepository
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

Spawn = Callable[[Callable[[], None]], None]

KIND_HEALTH_EDUCATION = "health_education"
KIND_EMBED = "embed"
KIND_LABELS = {KIND_HEALTH_EDUCATION: "匯入衛教內文", KIND_EMBED: "計算向量"}
STALE_MINUTES = 30
RECENT_LIMIT = 8


class KnowledgeJobService:
    def __init__(
        self,
        jobs: KnowledgeJobRepository,
        knowledge: KnowledgeService,
        upload_dir: str | Path,
        spawn: Spawn,
    ) -> None:
        self._jobs = jobs
        self._knowledge = knowledge
        self._upload_dir = Path(upload_dir)
        self.spawn = spawn  # 測試可換成同步執行

    # ---- 查詢 ----

    def overview(self, actor: Principal) -> dict[str, Any]:
        self._require_system(actor)
        self._reap_stale()
        return {
            "stats": self._knowledge.stats(),
            "active": self._public(self._jobs.active()),
            "recent": [self._public(j) for j in self._jobs.list_recent(RECENT_LIMIT)],
            "kinds": KIND_LABELS,
        }

    def get(self, actor: Principal, job_id: str) -> dict[str, Any]:
        self._require_system(actor)
        job = self._jobs.get(job_id)
        if job is None:
            raise ValidationError("找不到這個工作")
        return self._public(job)

    # ---- 啟動 ----

    def start_health_education(
        self,
        actor: Principal,
        stream: IO[bytes],
        filename: str,
        *,
        min_chars: int = 200,
        include_all: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """把上傳的 JSONL 存到 `upload_dir/<job_id>.jsonl`，背景匯入後刪檔。"""
        self._require_system(actor)
        if min_chars < 0 or (limit is not None and limit <= 0):
            raise ValidationError("min_chars 需 ≥ 0、limit 需 > 0")
        self._ensure_idle()
        job_id = uuid.uuid4().hex
        path = self._save_upload(stream, job_id)
        params = {
            "filename": filename,
            "min_chars": min_chars,
            "include_all": include_all,
            "limit": limit,
            "path": str(path),
        }
        return self._create_and_spawn(actor, KIND_HEALTH_EDUCATION, job_id, params)

    def start_embed(self, actor: Principal, *, batch: int = 200) -> dict[str, Any]:
        self._require_system(actor)
        if not 1 <= batch <= 1000:
            raise ValidationError("batch 需介於 1 到 1000")
        self._ensure_idle()
        stats = self._knowledge.stats()
        pending = stats["chunks"] - stats["embedded"]
        if pending <= 0:
            raise ValidationError("所有片段都已有向量，不需要再計算")
        params = {"batch": batch, "pending": pending}
        return self._create_and_spawn(actor, KIND_EMBED, uuid.uuid4().hex, params)

    # ---- 執行（背景執行緒內、已有 app context）----

    def run(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job["status"] != "queued":
            return
        self._jobs.update(job_id, status="running", started_at=_now())
        params = job.get("params") or {}
        try:
            if job["kind"] == KIND_HEALTH_EDUCATION:
                result = self._run_health_education(job_id, params)
            elif job["kind"] == KIND_EMBED:
                result = self._run_embed(job_id, params)
            else:
                raise ValidationError(f"未知的工作類型 {job['kind']}")
        except Exception as exc:  # noqa: BLE001 - 任何錯誤都要落到 failed，不能讓工作卡在 running
            message = exc.message if isinstance(exc, AppError) else str(exc) or type(exc).__name__
            logger.exception("Knowledge job %s (%s) failed", job_id, job["kind"])
            self._jobs.update(job_id, status="failed", error=message[:2000], finished_at=_now())
        else:
            self._jobs.update(job_id, status="succeeded", result=result, finished_at=_now())
        finally:
            self._cleanup_upload(params.get("path"))

    def _run_health_education(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        actor = Principal(user_id="system", role=Role.SUPER_ADMIN)

        def progress(created: int, read: int) -> None:
            self._jobs.update(job_id, progress={"created": created, "read": read})

        return self._knowledge.import_health_education(
            actor,
            Path(params["path"]),
            min_chars=int(params.get("min_chars", 200)),
            classified_only=not params.get("include_all", False),
            limit=params.get("limit"),
            progress=progress,
        )

    def _run_embed(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        batch = int(params.get("batch", 200))
        total = int(params.get("pending", 0))
        done = 0
        while True:
            # 每批獨立呼叫，讓進度能寫回；embed_pending 只處理還沒有向量的片段，可重入
            count = self._knowledge.embed_pending(batch=batch, limit=batch)
            if count == 0:
                break
            done += count
            self._jobs.update(job_id, progress={"done": done, "total": total})
        return {"embedded": done}

    # ---- helpers ----

    def _create_and_spawn(
        self, actor: Principal, kind: str, job_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        job = self._jobs.create(
            id=job_id, kind=kind, status="queued", params=params, created_by=actor.user_id
        )
        self.spawn(lambda: self.run(job_id))
        return self._public(self._jobs.get(job_id) or job)

    def _ensure_idle(self) -> None:
        self._reap_stale()
        active = self._jobs.active()
        if active:
            raise ConflictError(
                f"已有「{KIND_LABELS.get(active['kind'], active['kind'])}」工作進行中，請等它完成"
            )

    def _reap_stale(self) -> None:
        """執行程序重啟後遺留的 running 工作：超過 STALE_MINUTES 沒更新就標記中斷。"""
        active = self._jobs.active()
        if not active:
            return
        updated_at = datetime.fromisoformat(active["updated_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if _now() - updated_at > timedelta(minutes=STALE_MINUTES):
            self._jobs.update(
                active["id"],
                status="failed",
                error=f"超過 {STALE_MINUTES} 分鐘沒有進度，視為執行程序中斷",
                finished_at=_now(),
            )
            self._cleanup_upload((active.get("params") or {}).get("path"))

    def _save_upload(self, stream: IO[bytes], job_id: str) -> Path:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        path = self._upload_dir / f"{job_id}.jsonl"
        with path.open("wb") as target:
            shutil.copyfileobj(stream, target)
        if path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            raise ValidationError("上傳的檔案是空的")
        return path

    @staticmethod
    def _cleanup_upload(path: str | None) -> None:
        if path:
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def _require_system(actor: Principal) -> None:
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以執行知識庫維護工作")

    @staticmethod
    def _public(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        params = dict(job.get("params") or {})
        params.pop("path", None)  # 伺服器暫存路徑不外露
        return {**job, "params": params, "kind_label": KIND_LABELS.get(job["kind"], job["kind"])}


def _now() -> datetime:
    return datetime.now(UTC)
