"""知識庫（後台稱「衛教資訊」）：文件 CRUD、PDF 匯入、切段、向量計算、混合檢索。

- **管理以院所為單位、檢索不分院所**：每份文件屬於一家院所（`hospital_id`），
  由該院所具 EDUCATION_CONTENT_MANAGE 的人維護，院所管理員在後台只看得到自己院所的文件；
  通用文件（hospital_id 為空）只有 SUPER_ADMIN 看得到、能改。
  檢索時所有院所 + 通用的片段是同一個共用池，病人自己院所的內容優先
  （`retrieve(prefer_hospital_ids=)`），本院沒有相關內容時由其他院所 / 通用的補上。
- 檢索 = 關鍵字（tsvector / BM25）+ 語意（OpenAI embedding + pgvector）各取候選，
  以 RRF 合併，同一份文件最多留 2 個片段。沒有向量（未設 EMBEDDING_MODEL 或尚未計算）時只用關鍵字。
  有科別（`department`）時再多兩個「只在該科別內」的排序清單一起融合 → 同科別加權、不硬過濾。
- **資料來源只有 PDF 與後台手動新增**（`import_pdfs()`）：一個 PDF 一篇文件，標題 = 檔名，
  科別與片段大小由上傳的人選（`CHUNK_SIZES`，存在 `meta.chunk_size`），
  同院所同檔名重傳 = 更新。院所管理員可以對本院文件換片段大小重切（`rechunk()`）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from app.core.exceptions import (
    AppError,
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.domain.enums import Permission, Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.domain.retrieval import (
    CHUNK_TARGET_CHARS,
    chunk_document,
    clean_text,
    content_hash,
    department_in_text,
    rrf_fuse,
    tokenize,
)
from app.repositories.hospital_repository import HospitalRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.utils.pdf import extract_pdf_text, is_garbled_text

logger = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "service": "服務說明",
    "faq": "常見問題",
    "symptom": "症狀與就醫",
    "medication": "用藥",
    "education": "衛教",
    "hospital": "院所資訊",
}
# 片段大小（目標字數）：上傳 PDF / 重切時由院所管理員選；
# 硬上限、下限由 `chunk_document()` 依比例推得
CHUNK_SIZES: dict[str, int] = {"small": 300, "medium": CHUNK_TARGET_CHARS, "large": 600}
CHUNK_SIZE_LABELS: dict[str, str] = {
    "small": "小（約 300 字，問答精準、上下文少）",
    "medium": "中（約 400 字）",
    "large": "大（約 600 字，上下文完整）",
}
DEFAULT_CHUNK_SIZE = "medium"
MAX_CONTENT_CHARS = 200_000  # 一份文件的內文上限（PDF 擷取後 / 後台手動輸入）
MAX_DEPARTMENT_CHARS = 50
# 每個排序清單（關鍵字 / 語意 / 同科別）取幾個候選進 RRF；
# 最後只留 top_k（預設 4）個片段，每個清單 10 個候選就夠挑
CANDIDATES_PER_SOURCE = 10
MAX_CHUNKS_PER_DOCUMENT = 2
# 「本院優先」只加權夠相關的本院片段：分數要達到全庫第一名的這個比例才算，
# 否則本院只有不相干的內容時（語意檢索一定會回前幾名）也會被排到最前面。
# 兩個數字是經驗值、沒有用真實語料調過：語意 = 餘弦相似度，關鍵字 = ts_rank_cd / BM25 分數
PREFER_MIN_VECTOR_RATIO = 0.85
PREFER_MIN_KEYWORD_RATIO = 0.5
RECHUNK_BATCH = 500


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
        prefer_hospital_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """回傳最相關的片段。

        每個片段：{id, document_id, title, category, hospital_id, department, source, meta,
        heading, kind, content, score}。
        `hospital_ids`：None = 不限院所（預設，共用池）；
        list = 只查列出的範圍（`None` 元素 = 通用文件）。
        `department` 有值時同科別的片段加權（多兩個排序清單進 RRF）。
        `prefer_hospital_ids`（病人自己的院所）有值時，這些院所「夠相關」的片段再多兩個排序清單
        → 本院優先、不硬過濾；本院沒有相關內容時結果就是其他院所 / 通用的。
        """
        scope = hospital_ids
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
        if prefer_hospital_ids:
            own_scope: list[str | None] = list(prefer_hospital_ids)
            own_keyword = self._documents.search_keyword(
                query, tokens, hospital_ids=own_scope, limit=CANDIDATES_PER_SOURCE
            )
            own_semantic = self._semantic_candidates(vector, own_scope)
            rankings.append(self._relevant_enough(own_keyword, keyword, PREFER_MIN_KEYWORD_RATIO))
            rankings.append(self._relevant_enough(own_semantic, semantic, PREFER_MIN_VECTOR_RATIO))
        by_id = {h["id"]: h for ranking in rankings for h in ranking}
        fused = rrf_fuse([[h["id"] for h in ranking] for ranking in rankings])
        hits: list[dict[str, Any]] = []
        per_document: dict[str, int] = {}
        seen_contents: set[str] = set()  # 新增：不限院所時，各院轉載的同一段內文只留一份
        for chunk_id, score in fused:
            hit = by_id[chunk_id]
            if per_document.get(hit["document_id"], 0) >= MAX_CHUNKS_PER_DOCUMENT:
                continue
            if hit["content"] in seen_contents:
                continue
            seen_contents.add(hit["content"])
            per_document[hit["document_id"]] = per_document.get(hit["document_id"], 0) + 1
            hits.append({**hit, "score": round(score, 4)})
            if len(hits) >= limit:
                break
        return hits

    @staticmethod
    def _relevant_enough(
        own: list[dict[str, Any]], reference: list[dict[str, Any]], ratio: float
    ) -> list[dict[str, Any]]:
        """本院候選只留分數 ≥ 全庫第一名 × `ratio` 的（兩個清單都已依分數由高到低排好）。"""
        if not reference:
            return own
        floor = reference[0]["score"] * ratio
        return [hit for hit in own if hit["score"] >= floor]

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
        self,
        vector: list[float] | None,
        scope: list[str | None] | None,
        *,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        if not vector:
            return []
        return self._documents.search_vector(
            vector, hospital_ids=scope, limit=CANDIDATES_PER_SOURCE, department=department
        )

    def detect_department(
        self, texts: Iterable[str | None], hospital_ids: list[str | None] | None
    ) -> str | None:
        """依序在 `texts` 裡找科別：先比對範圍內文件實際用到的科別（各院所自己的分類），
        再退回通用科別表與口語別名（`department_in_text`）；都沒有回 None。"""  # 新增
        names = sorted(self._documents.departments(hospital_ids), key=len, reverse=True)
        for text in texts:
            if not text:
                continue
            normalized = text.replace("臺", "台")
            own = next((n for n in names if n.replace("臺", "台") in normalized), None)
            if own or (own := department_in_text(text)):
                return own
        return None

    # ---- 向量 ----

    def embed_pending(
        self,
        *,
        batch: int = 200,
        limit: int | None = None,
        hospital_ids: list[str | None] | None = None,
    ) -> int:
        """替尚未有向量的片段計算 embedding；可重複執行，回傳本次完成數。

        `hospital_ids` 有給就只算那個範圍的片段（院所上傳 / 重切後只算本院的）。
        """
        client = self._llm() if self._llm else None
        if client is None or not getattr(client, "embedding_model", None):
            raise ValidationError("未設定 EMBEDDING_MODEL，無法計算向量")
        done = 0
        while limit is None or done < limit:
            rows = self._documents.pending_embeddings(
                min(batch, (limit - done) if limit else batch), hospital_ids=hospital_ids
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

    def _embed_quietly(self, hospital_id: str | None) -> None:
        """後台手動新增 / 修改文件後順手算該範圍還沒有的向量；算不了只記 log，不讓儲存失敗。"""
        try:
            self.embed_pending(hospital_ids=[hospital_id])
        except AppError as exc:
            logger.warning("Embedding skipped after document change: %s", exc.message)

    def stats(self, *, hospital_ids: list[str | None] | None = None) -> dict[str, int]:
        return self._documents.stats(hospital_ids=hospital_ids)

    def hospital_overview(self, actor: Principal) -> list[dict[str, Any]]:
        """各院所的文件 / 片段 / 已有向量數（`hospital_id` 為 None 的那列是通用文件）。

        SUPER_ADMIN 看全部院所；院方帳號只有自己院所那一列。片段多的排前面。
        """
        scope = AccessPolicy.hospital_scope(actor)
        rows = self._documents.stats_by_hospital(hospital_ids=None if scope is None else [scope])
        names = (
            self._hospitals.get_many([r["hospital_id"] for r in rows if r["hospital_id"]])
            if self._hospitals
            else {}
        )
        for row in rows:
            hospital = names.get(row["hospital_id"])
            row["hospital_name"] = hospital["name"] if hospital else None
        return sorted(rows, key=lambda r: (-r["chunks"], r["hospital_id"] or ""))

    def chunk_options(self) -> dict[str, Any]:
        """片段大小選項（後台下拉用）。"""
        return {
            "default": DEFAULT_CHUNK_SIZE,
            "sizes": [
                {"key": key, "chars": chars, "label": CHUNK_SIZE_LABELS[key]}
                for key, chars in CHUNK_SIZES.items()
            ],
        }

    def departments(self, actor: Principal) -> list[str]:
        """actor 範圍內已用過的科別（上傳時的下拉建議；各院所可以有自己的分類）。"""
        scope = AccessPolicy.hospital_scope(actor)
        return sorted(self._documents.departments(None if scope is None else [scope]))

    # ---- 重切片段（換片段大小、或切段規則改了不想重新上傳時用）----

    def rechunk(
        self,
        actor: Principal,
        *,
        hospital_id: str | None = None,
        chunk_size: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """重建片段；向量會一併清掉（呼叫端負責再算）。

        範圍：院方帳號一律是自己院所；SUPER_ADMIN 給 `hospital_id` 就只重建那家，不給 = 全部。
        `chunk_size`（`CHUNK_SIZES` 的 key）有給就把範圍內文件都改成這個大小，
        沒給則每份文件維持自己原本的大小。
        """
        scope = self.rechunk_scope(actor, hospital_id)
        size = self.chunk_chars(chunk_size) if chunk_size else None
        ids = self._documents.document_ids(hospital_ids=None if scope is None else [scope])
        chunks = 0
        for start in range(0, len(ids), RECHUNK_BATCH):
            batch = ids[start : start + RECHUNK_BATCH]
            items: list[tuple[str, list[dict[str, Any]]]] = []
            resized: list[dict[str, Any]] = []
            for document_id in batch:
                doc = self._documents.get(document_id)
                if doc is None:
                    continue
                if size:
                    doc["meta"] = {**(doc.get("meta") or {}), "chunk_size": size}
                    resized.append({"id": document_id, "meta": doc["meta"]})
                items.append((document_id, self._chunks_for(doc)))
            if resized:
                self._documents.update_many(resized)
            self._documents.delete_chunks_many(batch)
            self._documents.add_chunks_many(items)
            chunks += sum(len(c) for _, c in items)
            if progress:
                progress(min(start + RECHUNK_BATCH, len(ids)), len(ids))
        return {"documents": len(ids), "chunks": chunks}

    def rechunk_scope(self, actor: Principal, hospital_id: str | None = None) -> str | None:
        """重切的院所範圍：院方帳號 = 自己院所；SUPER_ADMIN = 指定的院所或 None（全部）。"""
        self._require_manage(actor)
        scope = AccessPolicy.hospital_scope(actor)
        return scope if scope is not None else (hospital_id or None)

    # ---- CRUD（院方後台）----

    def list(self, actor: Principal) -> list[dict[str, Any]]:
        """後台列表：不含 content（內文很大），編輯時再以 get() 取單篇；每篇帶 `chunk_count`。

        院方帳號只列自己院所的文件（不含通用與別家的）；SUPER_ADMIN 看全部。
        """
        scope = AccessPolicy.hospital_scope(actor)
        hospital_ids = None if scope is None else [scope]
        rows = self._documents.list(hospital_ids=hospital_ids, with_content=False)
        counts = self._documents.chunk_counts(hospital_ids=hospital_ids)
        return [{**self._public(r), "chunk_count": counts.get(r["id"], 0)} for r in rows]

    def get(self, actor: Principal, document_id: str) -> dict[str, Any]:
        return self._public(self._get_owned(actor, document_id))

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
        self._embed_quietly(hospital_id)
        return self._public(doc)

    def update(self, actor: Principal, document_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._get_owned(actor, document_id)
        changes = self._clean(body, require_all=False)
        if "active" in body:
            changes["active"] = bool(body["active"])
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        if "content" in changes:
            changes["content_hash"] = content_hash(changes["content"])
        doc = self._documents.update(document_id, **changes)
        # 片段的斷詞含科別與標題，三者任一改了都要重切（向量跟著重算）
        if changes.keys() & {"content", "title", "department"}:
            self._reindex_document(doc)
            self._embed_quietly(doc.get("hospital_id"))
        return self._public(doc)

    def delete(self, actor: Principal, document_id: str) -> None:
        self._get_owned(actor, document_id)
        self._documents.delete(document_id)

    def search_preview(self, actor: Principal, query: str) -> list[dict[str, Any]]:
        """後台搜尋 = 本院病人問這句話時會檢索到的片段（共用池、本院優先）；SUPER_ADMIN 不加權。"""
        scope = AccessPolicy.hospital_scope(actor)
        return self.retrieve(query, prefer_hospital_ids=[scope] if scope else None)

    # ---- PDF 匯入 ----

    def import_pdfs(
        self,
        actor: Principal,
        files: list[tuple[Path, str]],
        *,
        hospital_id: str | None = None,
        department: str | None = None,
        chunk_size: str = DEFAULT_CHUNK_SIZE,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """衛教 PDF → 院所的知識文件（category=education），一個 PDF 一篇。

        `files` 是 (檔案路徑, 原始檔名)。標題 = 檔名（去副檔名）、`source` = 檔名：
        **同院所同檔名重傳就是更新**（內文、科別、片段大小都換成這次的）。
        歸屬（`resolve_import_hospital()`）：院所管理員一律是自己院所；
        SUPER_ADMIN 用 `hospital_id` 指定院所或留空（通用）。
        `department` 整批套用同一個科別（可空）；`chunk_size` 是 `CHUNK_SIZES` 的 key。
        讀不了的檔案（損毀 / 加密 / 掃描影像沒有文字層）列在 `errors`，不影響其他檔。
        向量不在這裡算，由呼叫端（`KnowledgeJobService`）匯入後接著算。
        """
        target = self.resolve_import_hospital(actor, hospital_id)
        size = self.chunk_chars(chunk_size)
        department = self.clean_department(department)
        hospital_name = self._hospital_name(target)
        summary: dict[str, Any] = {
            "files": len(files),
            "created": 0,
            "updated": 0,
            "chunks": 0,
            "errors": [],
        }
        for path, filename in files:
            try:
                pdf = extract_pdf_text(path.read_bytes())
                if len(pdf.text) > MAX_CONTENT_CHARS:
                    raise ValidationError(
                        f"內文超過 {MAX_CONTENT_CHARS} 字（{len(pdf.text)} 字），請拆成多個檔案"
                    )
                source = filename[:500]
                values = {
                    "title": Path(filename).stem[:200] or "衛教資料",
                    "category": "education",
                    "department": department,
                    "content": pdf.text,
                    "tags": [department] if department else [],
                    "source": source,
                    "content_hash": content_hash(pdf.text),
                    "meta": {
                        "hospital_name": hospital_name,
                        "department": department,
                        "imported_from": filename,
                        "pages": pdf.pages,
                        "chunk_size": size,
                    },
                }
                existing = next(
                    (
                        d
                        for d in self._documents.list_by_source(source)
                        if d.get("hospital_id") == target
                    ),
                    None,
                )
                if existing:
                    doc = self._documents.update(existing["id"], **values)
                    summary["updated"] += 1
                else:
                    doc = self._documents.create(
                        hospital_id=target, created_by=actor.user_id, **values
                    )
                    summary["created"] += 1
                chunks = self._chunks_for(doc)
                self._documents.replace_chunks(doc["id"], chunks)
                summary["chunks"] += len(chunks)
            except (ValidationError, OSError) as exc:
                summary["errors"].append(
                    {"file": filename, "message": getattr(exc, "message", str(exc))}
                )
            except Exception as exc:  # noqa: BLE001 - 寫入資料庫失敗等非預期錯誤
                # 新增：一個檔案出錯不能拖垮整批。先還原交易（否則同一個 session 之後的寫入
                # 全部會是 PendingRollbackError），這個檔案記成錯誤，其他檔案繼續
                self._documents.rollback()
                logger.error("PDF import failed for %s: %s", filename, str(exc)[:500])
                summary["errors"].append(
                    {
                        "file": filename,
                        "message": "寫入資料庫失敗，這個檔案已略過（詳見伺服器 log）",
                    }
                )
            if progress:
                progress(dict(summary))
        return summary

    def resolve_import_hospital(self, actor: Principal, requested: Any) -> str | None:
        """PDF 匯入的歸屬：院方帳號一律是自己院所；SUPER_ADMIN 可指定院所或留空（通用）。"""
        self._require_manage(actor)
        return self._resolve_hospital(actor, requested)

    @staticmethod
    def chunk_chars(chunk_size: str | None) -> int:
        """片段大小代碼 → 目標字數。"""
        key = str(chunk_size or DEFAULT_CHUNK_SIZE).strip()
        if key not in CHUNK_SIZES:
            raise ValidationError("片段大小不合法", details={"allowed": list(CHUNK_SIZES)})
        return CHUNK_SIZES[key]

    @staticmethod
    def clean_department(value: Any) -> str | None:
        department = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(department) > MAX_DEPARTMENT_CHARS:
            raise ValidationError(f"科別不可超過 {MAX_DEPARTMENT_CHARS} 字")
        return department or None

    # ---- helpers ----

    @staticmethod
    def _require_manage(actor: Principal) -> None:
        if not AccessPolicy.has_permission(actor, Permission.EDUCATION_CONTENT_MANAGE):
            raise AuthorizationError("沒有管理衛教資訊的權限")

    def _hospital_name(self, hospital_id: str | None) -> str | None:
        hospital = self._hospitals.get(hospital_id) if hospital_id and self._hospitals else None
        return hospital["name"] if hospital else None

    @staticmethod
    def _chunks_for(doc: dict[str, Any]) -> list[dict[str, Any]]:
        """文件 → 片段列（content 含標題路徑；tokens 含科別與標題，讓關鍵字檢索也吃得到）。

        片段大小取文件自己的 `meta.chunk_size`，沒有設定的用預設（`CHUNK_TARGET_CHARS`）。
        """
        head = " ".join(filter(None, (doc.get("department"), doc["title"])))
        max_chars = int((doc.get("meta") or {}).get("chunk_size") or CHUNK_TARGET_CHARS)
        rows: list[dict[str, Any]] = []
        for chunk in chunk_document(doc["content"], title=doc["title"], max_chars=max_chars):
            # PDF 表格常被抽成一格一兩個亂碼字，擷取時逐行看不出來、併成片段後才是一整行亂碼：
            # 把這種行拿掉，整個片段都是亂碼就不收，留著只會污染檢索
            content = "\n".join(
                line for line in chunk.content.split("\n") if not is_garbled_text(line)
            ).strip()
            if not content:
                continue
            rows.append(
                {
                    "content": content,
                    "heading": chunk.heading,
                    "kind": chunk.kind,
                    "tokens": tokenize(f"{head} {content}"),
                }
            )
        return rows

    def _reindex_document(self, doc: dict[str, Any]) -> None:
        self._documents.replace_chunks(doc["id"], self._chunks_for(doc))

    def _clean(self, body: dict[str, Any], *, require_all: bool) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if "title" in body or require_all:
            title = clean_text(str(body.get("title") or "")).strip()
            if not title or len(title) > 200:
                raise ValidationError("標題為必填（200 字內）")
            values["title"] = title
        if "content" in body or require_all:
            # clean_text：去掉 NUL 等控制字元（從別處貼上的內容可能夾帶，PostgreSQL 不接受）
            content = clean_text(str(body.get("content") or "")).strip()
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
        if "department" in body:  # 新增：手動新增 / 編輯的文件也能記科別
            values["department"] = self.clean_department(body.get("department"))
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
            # hospital_id 是外鍵，不存在的代碼要回 422 而不是資料庫 IntegrityError
            if hospital_id and self._hospitals and self._hospitals.get(hospital_id) is None:
                raise ValidationError("找不到這個院所代碼", details={"hospital_id": hospital_id})
            return hospital_id
        scope = AccessPolicy.hospital_scope(actor)
        if hospital_id and hospital_id != scope:
            raise AuthorizationError("不得建立其他院所的知識文件")
        return scope

    def _get_owned(self, actor: Principal, document_id: str) -> dict[str, Any]:
        """院方帳號只能讀寫自己院所的文件；通用與別家的文件只有 SUPER_ADMIN 能存取。"""
        doc = self._documents.get(document_id)
        if doc is None:
            raise NotFoundError("找不到這份文件")
        if actor.role is not Role.SUPER_ADMIN and doc.get(
            "hospital_id"
        ) != AccessPolicy.hospital_scope(actor):
            raise AuthorizationError("不得存取其他院所或通用的知識文件")
        return doc

    @staticmethod
    def _public(doc: dict[str, Any]) -> dict[str, Any]:
        meta = doc.get("meta") or {}
        return {
            **doc,
            "category_label": CATEGORIES.get(doc["category"], doc["category"]),
            "tags": doc.get("tags") or [],
            "meta": meta,
            "chunk_size": int(meta.get("chunk_size") or CHUNK_TARGET_CHARS),
        }
