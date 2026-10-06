"""衛教資訊（知識庫）背景工作：PDF 匯入、重建片段、向量計算。

這些都可能跑幾分鐘，不能在一個 HTTP 請求內做完，所以：
1. API 只負責收檔、建一列 `knowledge_jobs`、交給 `spawn` 在背景執行緒跑 `run()`；
2. 執行中定期把計數寫回 `progress`，頁面輪詢 `GET /api/v1/admin/knowledge/jobs` 顯示；
3. 同一時間只允許一個工作（避免同時匯入 + 算向量互相干擾）；
   執行程序若中途被重啟，超過 `STALE_MINUTES` 沒更新的工作視為中斷，讓下一個工作能開始。
院所管理員可以匯入 PDF、對本院文件換片段大小重切，做完**自動接著算本院新片段的向量**；
院方帳號只看得到自己院所的工作與統計。全庫「計算向量」只有 SUPER_ADMIN 能按。
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
from app.domain.rbac import AccessPolicy
from app.repositories.knowledge_repository import KnowledgeJobRepository
from app.services.knowledge_service import DEFAULT_CHUNK_SIZE, KnowledgeService

logger = logging.getLogger(__name__)

Spawn = Callable[[Callable[[], None]], None]

KIND_PDF_IMPORT = "pdf_import"
KIND_RECHUNK = "rechunk"
KIND_EMBED = "embed"
KIND_LABELS = {
    KIND_PDF_IMPORT: "匯入衛教 PDF",
    KIND_RECHUNK: "重建片段",
    KIND_EMBED: "計算向量",
}
MAX_UPLOAD_FILES = 500
EMBED_BATCH = 200
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
        """院方帳號：統計與最近工作只含自己院所；別人的工作進行中只回 `busy`，不露內容。

        另帶上傳表單要用的選項：片段大小、範圍內用過的科別。
        """
        scope = AccessPolicy.hospital_scope(actor)
        self._reap_stale()
        active = self._jobs.active()
        recent = self._jobs.list_recent(RECENT_LIMIT if scope is None else RECENT_LIMIT * 5)
        if scope is not None:
            recent = [j for j in recent if self._owned_by(j, scope)][:RECENT_LIMIT]
        return {
            "stats": self._knowledge.stats(hospital_ids=None if scope is None else [scope]),
            "active": self._public(active if active and self._owned_by(active, scope) else None),
            "busy": active is not None,
            "recent": [self._public(j) for j in recent],
            "kinds": KIND_LABELS,
            "chunk_options": self._knowledge.chunk_options(),
            "departments": self._knowledge.departments(actor),
        }

    def get(self, actor: Principal, job_id: str) -> dict[str, Any]:
        scope = AccessPolicy.hospital_scope(actor)
        job = self._jobs.get(job_id)
        if job is None or not self._owned_by(job, scope):
            raise ValidationError("找不到這個工作")
        return self._public(job)

    # ---- 啟動 ----

    def start_pdf_import(
        self,
        actor: Principal,
        uploads: list[tuple[IO[bytes], str]],
        *,
        hospital_id: str | None = None,
        department: str | None = None,
        chunk_size: str | None = None,
    ) -> dict[str, Any]:
        """把上傳的 PDF 存到 `upload_dir/<job_id>/`，背景匯入、切段、算向量後整個目錄刪掉。

        歸屬、科別、片段大小在這裡用真正的操作者驗證（院所管理員 = 自己院所；
        SUPER_ADMIN 可指定院所或留空為通用），背景執行時照 `params` 寫入。
        """
        target = self._knowledge.resolve_import_hospital(actor, hospital_id)
        chunk_size = chunk_size or DEFAULT_CHUNK_SIZE
        self._knowledge.chunk_chars(chunk_size)
        department = self._knowledge.clean_department(department)
        if not uploads or len(uploads) > MAX_UPLOAD_FILES:
            raise ValidationError(f"請上傳 1 到 {MAX_UPLOAD_FILES} 個 PDF 檔")
        self._ensure_idle()
        job_id = uuid.uuid4().hex
        folder = self._save_uploads(uploads, job_id)
        params = {
            "filenames": [Path(name).name for _, name in uploads],
            "path": str(folder),
            "hospital_id": target,
            "department": department,
            "chunk_size": chunk_size,
        }
        return self._create_and_spawn(actor, KIND_PDF_IMPORT, job_id, params)

    def start_rechunk(self, actor: Principal, *, chunk_size: str | None = None) -> dict[str, Any]:
        """重建片段：院方帳號只重建自己院所的文件，SUPER_ADMIN 重建全部。

        `chunk_size` 有給就把範圍內文件都改成這個大小，沒給維持各文件原本的大小。
        院所範圍的重切做完會自動接著算向量；全庫重切（SUPER_ADMIN）的向量要另外按「計算向量」。
        """
        scope = self._knowledge.rechunk_scope(actor)
        if chunk_size:
            self._knowledge.chunk_chars(chunk_size)
        self._ensure_idle()
        stats = self._knowledge.stats(hospital_ids=None if scope is None else [scope])
        if stats["documents"] == 0:
            raise ValidationError("還沒有文件可以重建片段")
        params = {
            "documents": stats["documents"],
            "hospital_id": scope,
            "chunk_size": chunk_size or None,
        }
        return self._create_and_spawn(actor, KIND_RECHUNK, uuid.uuid4().hex, params)

    def start_embed(self, actor: Principal, *, batch: int = EMBED_BATCH) -> dict[str, Any]:
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
            if job["kind"] == KIND_PDF_IMPORT:
                result = self._run_pdf_import(job_id, params)
            elif job["kind"] == KIND_RECHUNK:
                result = self._run_rechunk(job_id, params)
            elif job["kind"] == KIND_EMBED:
                result = self._run_embed(job_id, params)
            else:
                raise ValidationError(f"未知的工作類型 {job['kind']}")
        except Exception as exc:  # noqa: BLE001 - 任何錯誤都要落到 failed，不能讓工作卡在 running
            message = exc.message if isinstance(exc, AppError) else str(exc) or type(exc).__name__
            logger.exception("Knowledge job %s (%s) failed", job_id, job["kind"])
            # 修改：資料庫錯誤會讓 session 停在「待還原」狀態，不先 rollback 的話下面這行
            # 也會失敗，工作就永遠卡在 running、擋住之後所有的匯入
            self._jobs.rollback()
            self._jobs.update(job_id, status="failed", error=message[:2000], finished_at=_now())
        else:
            self._jobs.update(job_id, status="succeeded", result=result, finished_at=_now())
        finally:
            self._cleanup_upload(params.get("path"))

    def _run_pdf_import(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
        folder = Path(params["path"])
        # 存檔時用序號當檔名（`_save_uploads`），原始檔名從 params 對回來當標題
        files = [(folder / f"{index}.pdf", name) for index, name in enumerate(params["filenames"])]

        def progress(summary: dict[str, Any]) -> None:
            self._jobs.update(job_id, progress=summary)

        result = self._knowledge.import_pdfs(
            actor,
            files,
            hospital_id=params.get("hospital_id"),
            department=params.get("department"),
            chunk_size=params.get("chunk_size") or DEFAULT_CHUNK_SIZE,
            progress=progress,
        )
        result.update(self._embed_scope(job_id, result, [params.get("hospital_id")]))
        return result

    def _run_rechunk(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
        hospital_id = params.get("hospital_id")

        def progress(done: int, total: int) -> None:
            self._jobs.update(job_id, progress={"done": done, "total": total})

        result: dict[str, Any] = self._knowledge.rechunk(
            actor, hospital_id=hospital_id, chunk_size=params.get("chunk_size"), progress=progress
        )
        if hospital_id:  # 院所範圍的重切：接著算本院的向量
            result.update(self._embed_scope(job_id, result, [hospital_id]))
        return result

    def _embed_scope(
        self, job_id: str, summary: dict[str, Any], hospital_ids: list[str | None]
    ) -> dict[str, Any]:
        """匯入 / 重切後接著算該範圍還沒有的向量；算不了（未設金鑰、OpenAI 失敗）不讓整個工作失敗，
        片段已存好、關鍵字檢索可用，原因記在結果的 `embed_error`。"""
        done = 0
        try:
            while count := self._knowledge.embed_pending(
                batch=EMBED_BATCH, limit=EMBED_BATCH, hospital_ids=hospital_ids
            ):
                done += count
                self._jobs.update(job_id, progress={**summary, "embedded": done})
        except AppError as exc:
            logger.warning("Knowledge job %s: embedding skipped: %s", job_id, exc.message)
            return {"embedded": done, "embed_error": exc.message[:500]}
        return {"embedded": done}

    def _run_embed(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        batch = int(params.get("batch", EMBED_BATCH))
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

    def _save_uploads(self, uploads: list[tuple[IO[bytes], str]], job_id: str) -> Path:
        """存成 `<upload_dir>/<job_id>/<序號>.pdf`（原始檔名留在 params，不拿來當路徑）。"""
        folder = self._upload_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        for index, (stream, name) in enumerate(uploads):
            if not name.lower().endswith(".pdf"):
                shutil.rmtree(folder, ignore_errors=True)
                raise ValidationError(f"只接受 .pdf：{name}")
            path = folder / f"{index}.pdf"
            with path.open("wb") as target:
                shutil.copyfileobj(stream, target)
            if path.stat().st_size == 0:
                shutil.rmtree(folder, ignore_errors=True)
                raise ValidationError(f"上傳的檔案是空的：{name}")
        return folder

    @staticmethod
    def _cleanup_upload(path: str | None) -> None:
        if not path:
            return
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)

    @staticmethod
    def _owned_by(job: dict[str, Any], scope: str | None) -> bool:
        """`scope` 為 None（SUPER_ADMIN）看全部；院方帳號只看歸自己院所的匯入 / 重切工作。"""
        if scope is None:
            return True
        return (job.get("params") or {}).get("hospital_id") == scope

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
