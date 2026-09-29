"""知識庫：文件 CRUD（院方後台）、Markdown / 衛教 txt 匯入、重切片段、向量計算、混合檢索。

- 通用文件（hospital_id 為空）只有 SUPER_ADMIN 能改；
  院所文件由該院所具 EDUCATION_CONTENT_MANAGE 的人維護。
- 檢索 = 關鍵字（tsvector / BM25）+ 語意（OpenAI embedding + pgvector）各取候選，
  以 RRF 合併，同一份文件最多留 2 個片段。沒有向量（未設 EMBEDDING_MODEL 或尚未計算）時只用關鍵字。
  有科別（`department`）時再多兩個「只在該科別內」的排序清單一起融合 → 同科別加權、不硬過濾
  （科別分類正確率約 9 成，硬過濾會漏）。
- 衛教資料來源是「依科別分類」的 txt（每科一檔、`====` 分隔、四行檔頭），
  見 `import_department_files()`。
"""

from __future__ import annotations

import logging
import re
import statistics
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from app.core.exceptions import (
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.domain.retrieval import (
    CHUNK_HARD_MAX_RATIO,
    CHUNK_MIN_RATIO,
    CHUNK_TARGET_CHARS,
    chunk_document,
    cjk_ratio,
    clean_lines,
    clean_text,
    collect_boilerplate,
    content_hash,
    rrf_fuse,
    tokenize,
)
from app.repositories.hospital_repository import HospitalRepository
from app.repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "service": "服務說明",
    "faq": "常見問題",
    "symptom": "症狀與就醫",
    "medication": "用藥",
    "education": "衛教",
    "hospital": "院所資訊",
}
MAX_CONTENT_CHARS = 20_000
CANDIDATES_PER_SOURCE = 20
MAX_CHUNKS_PER_DOCUMENT = 2
IMPORT_BATCH = 500
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# 依科別分類的衛教 txt：檔名「01_心臟內科.txt」，篇與篇用 60 個 = 分隔，每篇四行檔頭後空一行接內文
DEPARTMENT_RECORD_SEP = "=" * 60
_DEPARTMENT_FILENAME = re.compile(r"^(?P<code>\d{2})_(?P<department>.+)\.txt$")
# 修改：檔頭四行都是單行，用 [^\n]* 而不是 DOTALL 的 .*，
# 否則內文裡出現「網址：」「醫院：」行會把內文吃進檔頭欄位
_DEPARTMENT_HEADER = re.compile(
    r"標題：(?P<title>[^\n]*)\n醫院：(?P<hospital>[^\n（]*)(?:（(?P<level>[^）\n]*)）)?[^\n]*\n"
    r"(?:來源分類：(?P<category>[^\n]*)\n)?網址：(?P<url>\S+)[^\n]*\n"
)
SKIP_DEPARTMENT_PREFIXES = ("非衛教",)  # 院務消息 / 活動 / 醫師介紹整檔不收
UNCLASSIFIED_DEPARTMENT = "未分類"  # 照收，但 department 留空


class KnowledgeService:
    def __init__(
        self,
        documents: KnowledgeRepository,
        llm: Callable[[], Any] | None = None,
        *,
        top_k: int = 4,
        hospitals: HospitalRepository | None = None,
    ) -> None:
        self._documents = documents
        self._llm = llm
        self._top_k = top_k
        self._hospitals = hospitals  # 新增：驗證 SUPER_ADMIN 指定的院所代碼存在

    # ---- 檢索 ----

    def retrieve(
        self,
        query: str,
        *,
        hospital_ids: list[str | None] | None = None,
        top_k: int | None = None,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        """回傳最相關的片段。

        每個片段：{id, document_id, title, category, hospital_id, department, source, meta,
        heading, kind, content, score}。
        `department` 有值時同科別的片段加權（多兩個排序清單進 RRF）。
        """
        scope = hospital_ids or [None]
        limit = top_k or self._top_k
        tokens = tokenize(query)
        keyword = self._documents.search_keyword(
            query, tokens, hospital_ids=scope, limit=CANDIDATES_PER_SOURCE
        )
        vector = self._query_vector(query)
        semantic = self._semantic_candidates(vector, scope)
        rankings = [keyword, semantic]
        if department:
            rankings.append(
                self._documents.search_keyword(
                    query,
                    tokens,
                    hospital_ids=scope,
                    limit=CANDIDATES_PER_SOURCE,
                    department=department,
                )
            )
            rankings.append(self._semantic_candidates(vector, scope, department=department))
        by_id = {h["id"]: h for ranking in rankings for h in ranking}
        fused = rrf_fuse([[h["id"] for h in ranking] for ranking in rankings])
        hits: list[dict[str, Any]] = []
        per_document: dict[str, int] = {}
        for chunk_id, score in fused:
            hit = by_id[chunk_id]
            if per_document.get(hit["document_id"], 0) >= MAX_CHUNKS_PER_DOCUMENT:
                continue
            per_document[hit["document_id"]] = per_document.get(hit["document_id"], 0) + 1
            hits.append({**hit, "score": round(score, 4)})
            if len(hits) >= limit:
                break
        return hits

    def _query_vector(self, query: str) -> list[float] | None:
        client = self._llm() if self._llm else None
        if client is None or not getattr(client, "embedding_model", None):
            return None
        try:
            vectors = client.embed([query])
        except ExternalServiceError as exc:
            logger.warning("Query embedding failed, keyword-only retrieval: %s", exc.message)
            return None
        return vectors[0] if vectors else None

    def _semantic_candidates(
        self, vector: list[float] | None, scope: list[str | None], *, department: str | None = None
    ) -> list[dict[str, Any]]:
        if not vector:
            return []
        return self._documents.search_vector(
            vector, hospital_ids=scope, limit=CANDIDATES_PER_SOURCE, department=department
        )

    # ---- 向量 ----

    def embed_pending(self, *, batch: int = 200, limit: int | None = None) -> int:
        """替尚未有向量的片段計算 embedding；可重複執行，回傳本次完成數。"""
        client = self._llm() if self._llm else None
        if client is None or not getattr(client, "embedding_model", None):
            raise ValidationError("未設定 EMBEDDING_MODEL，無法計算向量")
        done = 0
        while limit is None or done < limit:
            rows = self._documents.pending_embeddings(
                min(batch, (limit - done) if limit else batch)
            )
            if not rows:
                break
            vectors = client.embed([self._embedding_text(r) for r in rows])
            self._documents.set_embeddings(list(zip([r["id"] for r in rows], vectors, strict=True)))
            done += len(rows)
            logger.info("Embedded %s chunks (%s total this run)", len(rows), done)
        return done

    @staticmethod
    def _embedding_text(row: dict[str, Any]) -> str:
        """向量輸入 = 科別 › 標題 + 片段（片段本身已含標題路徑）。"""
        head = f"{row['department']} › {row['title']}" if row.get("department") else row["title"]
        return f"{head}\n{row['content']}"

    def stats(self) -> dict[str, int]:
        return self._documents.stats()

    # ---- 重切片段（切段規則改了、不想重新上傳資料時用）----

    def rechunk_all(
        self, actor: Principal, *, progress: Callable[[int, int], None] | None = None
    ) -> dict[str, int]:
        """用目前的切段規則重建所有文件的片段；向量會一併清掉，要再跑一次「計算向量」。"""
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以重建片段")
        ids = self._documents.document_ids()
        chunks = 0
        for start in range(0, len(ids), IMPORT_BATCH):
            batch = ids[start : start + IMPORT_BATCH]
            items: list[tuple[str, list[dict[str, Any]]]] = []
            for document_id in batch:
                doc = self._documents.get(document_id)
                if doc is None:
                    continue
                items.append((document_id, self._chunks_for(doc)))
            self._documents.delete_chunks_many(batch)
            self._documents.add_chunks_many(items)
            chunks += sum(len(c) for _, c in items)
            if progress:
                progress(min(start + IMPORT_BATCH, len(ids)), len(ids))
        return {"documents": len(ids), "chunks": chunks}

    # ---- CRUD（院方後台）----

    def list(self, actor: Principal) -> list[dict[str, Any]]:
        """後台列表：不含 content（上萬篇內文會有幾十 MB），編輯時再以 get() 取單篇。"""
        scope = AccessPolicy.hospital_scope(actor)
        rows = self._documents.list(
            hospital_ids=None if scope is None else [None, scope], with_content=False
        )
        return [self._public(r) for r in rows]

    def get(self, actor: Principal, document_id: str) -> dict[str, Any]:
        return self._public(self._get_visible(actor, document_id))

    def create(self, actor: Principal, body: dict[str, Any]) -> dict[str, Any]:
        values = self._clean(body, require_all=True)
        hospital_id = self._resolve_hospital(actor, body.get("hospital_id"))
        doc = self._documents.create(
            hospital_id=hospital_id,
            created_by=actor.user_id,
            content_hash=content_hash(values["content"]),
            **values,
        )
        self._reindex_document(doc)
        return self._public(doc)

    def update(self, actor: Principal, document_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._get_editable(actor, document_id)
        changes = self._clean(body, require_all=False)
        if "active" in body:
            changes["active"] = bool(body["active"])
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        if "content" in changes:
            changes["content_hash"] = content_hash(changes["content"])
        doc = self._documents.update(document_id, **changes)
        if "content" in changes or "title" in changes:
            self._reindex_document(doc)
        return self._public(doc)

    def delete(self, actor: Principal, document_id: str) -> None:
        self._get_editable(actor, document_id)
        self._documents.delete(document_id)

    def find_by_source(self, actor: Principal, source: str) -> dict[str, Any] | None:
        """後台重複上傳同名檔案 → 更新同一份（只在 actor 看得到的範圍內）。"""
        for doc in self._documents.list_by_source(source):  # 修改：挑 actor 可編輯的那一份
            try:
                return self._get_editable(actor, doc["id"])
            except AuthorizationError:
                continue
        return None

    def search_preview(self, actor: Principal, query: str) -> list[dict[str, Any]]:
        scope = AccessPolicy.hospital_scope(actor)
        return self.retrieve(query, hospital_ids=None if scope is None else [scope])

    # ---- Markdown 匯入 ----

    def import_path(
        self, actor: Principal, path: Path, *, hospital_id: str | None = None
    ) -> dict[str, Any]:
        """匯入單一 .md 或整個目錄；`source` = 檔名，重匯時更新同一份文件。"""
        files = sorted(path.glob("*.md")) if path.is_dir() else [path]
        created = updated = 0
        errors: list[dict[str, str]] = []
        for file in files:
            try:
                fields = parse_markdown(file.read_text(encoding="utf-8"), default_title=file.stem)
                fields["source"] = file.name
                target_hospital = self._resolve_hospital(
                    actor, fields.pop("hospital_id", None) or hospital_id
                )
                values = self._clean(fields, require_all=True)
                values["content_hash"] = content_hash(values["content"])
                existing = next(
                    (
                        d
                        for d in self._documents.list_by_source(file.name)
                        if d.get("hospital_id") == target_hospital
                    ),
                    None,
                )
                if existing:
                    doc = self._documents.update(existing["id"], **values)
                    updated += 1
                else:
                    doc = self._documents.create(
                        hospital_id=target_hospital, created_by=actor.user_id, **values
                    )
                    created += 1
                self._reindex_document(doc)
            except (ValidationError, AuthorizationError, UnicodeDecodeError) as exc:
                errors.append({"file": file.name, "message": getattr(exc, "message", str(exc))})
        return {"created": created, "updated": updated, "errors": errors}

    # ---- 衛教 txt 匯入（依科別分類，每科一檔）----

    def import_department_files(
        self,
        actor: Principal,
        paths: list[Path],
        *,
        min_chars: int = 200,
        limit: int | None = None,
        progress: Callable[[dict[str, int]], None] | None = None,
    ) -> dict[str, Any]:
        """依科別分類的衛教 txt → 通用知識文件（category=education，`department` = 檔名的科別）。

        兩遍掃描：第一遍統計全語料的網站導覽樣板行（`collect_boilerplate`），第二遍清理、切段、寫入。
        篩選：內文 ≥ min_chars、中文比例 ≥ 0.4、內文不重複（跨醫院常互相轉載）。
        `source` = 原始網址：已存在的文件**更新**（補科別、依新規則重切），沒有的新增，所以可重跑。
        「非衛教」檔整檔略過；「未分類」照收但科別留空。
        """
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以匯入衛教資料")
        files = [(p, self._department_of(p)) for p in paths]
        files = [(p, d) for p, d in files if d is not False]
        summary: dict[str, Any] = {
            "files": len(files),
            "read": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "duplicates": 0,
        }
        boilerplate = collect_boilerplate(
            clean_lines(r["text"], title=r["title"])
            for path, _ in files
            for r in _iter_department_records(path)
        )
        summary["boilerplate_lines"] = len(boilerplate)
        seen_hashes = self._documents.existing_hashes()
        existing = self._documents.source_index()  # 修改：只取 source → id，不把全庫內文載進記憶體
        handled_urls: set[str] = set()  # 新增：同一次匯入內同網址只處理一次
        pending_new: list[dict[str, Any]] = []
        pending_update: list[dict[str, Any]] = []
        pending_chunks: list[tuple[str, list[dict[str, Any]]]] = []
        chunk_lengths: list[int] = []

        def flush() -> None:
            if pending_new:
                self._documents.create_many(list(pending_new))
            if pending_update:
                self._documents.delete_chunks_many([d["id"] for d in pending_update])
                self._documents.update_many(list(pending_update))
            self._documents.add_chunks_many(list(pending_chunks))
            pending_new.clear()
            pending_update.clear()
            pending_chunks.clear()
            if progress:
                progress(dict(summary))

        for path, department in files:
            for record in _iter_department_records(path):
                summary["read"] += 1
                if limit is not None and summary["created"] + summary["updated"] >= limit:
                    break
                text = record["text"]
                if len(text) < min_chars or cjk_ratio(text) < 0.4:
                    summary["skipped"] += 1
                    continue
                digest = content_hash(text)
                if record["url"] in handled_urls or (
                    digest in seen_hashes and record["url"] not in existing
                ):
                    summary["duplicates"] += 1
                    continue
                seen_hashes.add(digest)
                handled_urls.add(record["url"])
                doc = self._department_document(record, department, digest, actor)
                chunks = self._chunks_for(doc, boilerplate=boilerplate)
                if not chunks:
                    summary["skipped"] += 1
                    continue
                document_id = existing.get(record["url"])
                if document_id:
                    doc.pop("created_by")
                    pending_update.append({**doc, "id": document_id})
                    summary["updated"] += 1
                else:
                    document_id = doc["id"]
                    existing[record["url"]] = document_id
                    pending_new.append(doc)
                    summary["created"] += 1
                pending_chunks.append((document_id, chunks))
                chunk_lengths.extend(len(c["content"]) for c in chunks)
                if len(pending_new) + len(pending_update) >= IMPORT_BATCH:
                    flush()
        flush()
        summary["chunks"] = chunk_stats(chunk_lengths)
        return summary

    @staticmethod
    def _department_of(path: Path) -> str | None | bool:
        """檔名 → 科別；未分類回 None；非衛教（整檔略過）回 False。"""
        match = _DEPARTMENT_FILENAME.match(path.name)
        if not match:
            raise ValidationError(f"檔名格式應為「01_科別.txt」：{path.name}")
        department = match.group("department").strip()
        if department.startswith(SKIP_DEPARTMENT_PREFIXES):
            return False
        return None if department == UNCLASSIFIED_DEPARTMENT else department[:50]

    @staticmethod
    def _department_document(
        record: dict[str, Any], department: str | None, digest: str, actor: Principal
    ) -> dict[str, Any]:
        title = record["title"] or record["hospital"] or "衛教資料"
        return {
            "id": uuid.uuid4().hex,
            "title": title[:200],
            "category": "education",
            "hospital_id": None,
            "department": department,
            "content": record["text"][:MAX_CONTENT_CHARS],
            "tags": [department] if department else [],
            "source": record["url"][:500],
            "content_hash": digest,
            "meta": {
                "hospital_name": record["hospital"] or None,
                "level": record["level"] or None,
                "department": department,
                "source_category": record["category"] or None,
                "imported_from": record["file"],
            },
            "active": True,
            "created_by": actor.user_id,
        }

    # ---- helpers ----

    @staticmethod
    def _chunks_for(
        doc: dict[str, Any], *, boilerplate: frozenset[str] | None = None
    ) -> list[dict[str, Any]]:
        """文件 → 片段列（content 含標題路徑；tokens 含科別與標題，讓關鍵字檢索也吃得到）。"""
        head = " ".join(filter(None, (doc.get("department"), doc["title"])))
        return [
            {
                "content": chunk.content,
                "heading": chunk.heading,
                "kind": chunk.kind,
                "tokens": tokenize(f"{head} {chunk.content}"),
            }
            for chunk in chunk_document(doc["content"], title=doc["title"], boilerplate=boilerplate)
        ]

    def _reindex_document(self, doc: dict[str, Any]) -> None:
        self._documents.replace_chunks(doc["id"], self._chunks_for(doc))

    @staticmethod
    def _clean(body: dict[str, Any], *, require_all: bool) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if "title" in body or require_all:
            title = str(body.get("title") or "").strip()
            if not title or len(title) > 200:
                raise ValidationError("標題為必填（200 字內）")
            values["title"] = title
        if "content" in body or require_all:
            content = str(body.get("content") or "").strip()
            if not content:
                raise ValidationError("內容為必填")
            if len(content) > MAX_CONTENT_CHARS:
                raise ValidationError(f"內容不可超過 {MAX_CONTENT_CHARS} 字")
            values["content"] = content
        if "category" in body or require_all:
            category = str(body.get("category") or "faq").strip()
            if category not in CATEGORIES:
                raise ValidationError("分類不合法", details={"allowed": list(CATEGORIES)})
            values["category"] = category
        if "tags" in body:
            raw = body.get("tags") or []
            if isinstance(raw, str):
                raw = [t for t in re.split(r"[,，\s]+", raw) if t]
            values["tags"] = [str(t).strip()[:30] for t in raw][:20]
        if "source" in body:
            source = str(body.get("source") or "").strip()
            values["source"] = source[:500] or None
        return values

    def _resolve_hospital(self, actor: Principal, requested: Any) -> str | None:
        """通用文件只有 SUPER_ADMIN 能建；院方帳號一律綁自己的院所。"""
        hospital_id = str(requested or "").strip() or None
        if actor.role is Role.SUPER_ADMIN:
            # 修改：hospital_id 是外鍵，不存在的代碼要回 422 而不是資料庫 IntegrityError
            if hospital_id and self._hospitals and self._hospitals.get(hospital_id) is None:
                raise ValidationError("找不到這個院所代碼", details={"hospital_id": hospital_id})
            return hospital_id
        scope = AccessPolicy.hospital_scope(actor)
        if hospital_id and hospital_id != scope:
            raise AuthorizationError("不得建立其他院所的知識文件")
        return scope

    def _get_visible(self, actor: Principal, document_id: str) -> dict[str, Any]:
        doc = self._documents.get(document_id)
        if doc is None:
            raise NotFoundError("找不到這份文件")
        if actor.role is not Role.SUPER_ADMIN and doc.get("hospital_id") not in (
            None,
            AccessPolicy.hospital_scope(actor),
        ):
            raise AuthorizationError("不得存取其他院所的知識文件")
        return doc

    def _get_editable(self, actor: Principal, document_id: str) -> dict[str, Any]:
        doc = self._get_visible(actor, document_id)
        if doc.get("hospital_id") is None and actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("通用知識文件只有系統管理員可以修改")
        return doc

    @staticmethod
    def _public(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            **doc,
            "category_label": CATEGORIES.get(doc["category"], doc["category"]),
            "tags": doc.get("tags") or [],
            "meta": doc.get("meta") or {},
        }


def _iter_department_records(path: Path) -> Iterator[dict[str, str]]:
    """一篇一個 dict：title / hospital / level / category / url / text / file；檔頭壞的略過。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.split(DEPARTMENT_RECORD_SEP)[1:]:
        match = _DEPARTMENT_HEADER.match(raw.lstrip("\n"))
        if not match:
            continue
        body = clean_text(raw.lstrip("\n")[match.end() :]).strip()
        yield {
            "title": clean_text(match.group("title") or "").strip(),
            "hospital": clean_text(match.group("hospital") or "").strip(),
            "level": (match.group("level") or "").strip(),
            "category": clean_text(match.group("category") or "").strip()[:200],
            "url": match.group("url").strip(),
            "text": body,
            "file": path.name,
        }


def chunk_stats(lengths: list[int]) -> dict[str, int]:
    """片段長度分佈摘要（匯入 / 重切結果回報），對照 `chunk_document()` 的目標 / 硬上限 / 下限。"""
    if not lengths:
        return {"count": 0}
    return {
        "count": len(lengths),
        "median": int(statistics.median(lengths)),
        "min": min(lengths),
        "max": max(lengths),
        "under_min": sum(1 for n in lengths if n < CHUNK_TARGET_CHARS * CHUNK_MIN_RATIO),
        "over_hard_max": sum(1 for n in lengths if n > CHUNK_TARGET_CHARS * CHUNK_HARD_MAX_RATIO),
    }


def parse_markdown(text: str, *, default_title: str) -> dict[str, Any]:
    """Markdown（可含 front matter：title / category / hospital_id / tags）→ 欄位。"""
    fields: dict[str, Any] = {}
    match = _FRONT_MATTER.match(text)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip().strip("'\"")
        text = text[match.end() :]
    body = text.strip()
    if not fields.get("title"):
        heading = re.match(r"^#\s+(.+)$", body, re.M)
        fields["title"] = heading.group(1).strip() if heading else default_title
    fields["content"] = body
    fields.setdefault("category", "faq")
    return fields
