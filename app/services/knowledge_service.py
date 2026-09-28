"""知識庫：文件 CRUD（院方後台）、Markdown / 衛教 JSONL 匯入、向量計算、混合檢索（RAG 的 R）。

- 通用文件（hospital_id 為空）只有 SUPER_ADMIN 能改；
  院所文件由該院所具 EDUCATION_CONTENT_MANAGE 的人維護。
- 檢索 = 關鍵字（tsvector / BM25）+ 語意（OpenAI embedding + pgvector）各取候選，
  以 RRF 合併，同一份文件最多留 2 個片段。沒有向量（未設 EMBEDDING_MODEL 或尚未計算）時只用關鍵字。
"""

from __future__ import annotations

import json
import logging
import re
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
    chunk_text,
    cjk_ratio,
    clean_text,
    content_hash,
    rrf_fuse,
    tokenize,
)
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


class KnowledgeService:
    def __init__(
        self,
        documents: KnowledgeRepository,
        llm: Callable[[], Any] | None = None,
        *,
        top_k: int = 4,
    ) -> None:
        self._documents = documents
        self._llm = llm
        self._top_k = top_k

    # ---- 檢索 ----

    def retrieve(
        self, query: str, *, hospital_ids: list[str | None] | None = None, top_k: int | None = None
    ) -> list[dict[str, Any]]:
        """回傳最相關的片段。

        每個片段：{id, document_id, title, category, hospital_id, source, meta, content, score}。
        """
        scope = hospital_ids or [None]
        limit = top_k or self._top_k
        keyword = self._documents.search_keyword(
            query, tokenize(query), hospital_ids=scope, limit=CANDIDATES_PER_SOURCE
        )
        semantic = self._semantic_candidates(query, scope)
        by_id = {h["id"]: h for h in keyword + semantic}
        fused = rrf_fuse([[h["id"] for h in keyword], [h["id"] for h in semantic]])
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

    def _semantic_candidates(self, query: str, scope: list[str | None]) -> list[dict[str, Any]]:
        client = self._llm() if self._llm else None
        if client is None or not getattr(client, "embedding_model", None):
            return []
        try:
            vectors = client.embed([query])
        except ExternalServiceError as exc:
            logger.warning("Query embedding failed, keyword-only retrieval: %s", exc.message)
            return []
        if not vectors:
            return []
        return self._documents.search_vector(
            vectors[0], hospital_ids=scope, limit=CANDIDATES_PER_SOURCE
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
            vectors = client.embed([f"{r['title']}\n{r['content']}" for r in rows])
            self._documents.set_embeddings(list(zip([r["id"] for r in rows], vectors, strict=True)))
            done += len(rows)
            logger.info("Embedded %s chunks (%s total this run)", len(rows), done)
        return done

    def stats(self) -> dict[str, int]:
        return self._documents.stats()

    # ---- CRUD（院方後台）----

    def list(self, actor: Principal) -> list[dict[str, Any]]:
        scope = AccessPolicy.hospital_scope(actor)
        rows = self._documents.list(hospital_ids=None if scope is None else [None, scope])
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
        doc = self._documents.get_by_source(source)
        if doc is None:
            return None
        try:
            return self._get_editable(actor, doc["id"])
        except AuthorizationError:
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
                existing = self._documents.get_by_source(file.name)
                if existing and existing.get("hospital_id") == target_hospital:
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

    # ---- 衛教 JSONL 匯入（data/衛教內文資料/health_education_content.jsonl）----

    def import_health_education(
        self,
        actor: Principal,
        path: Path,
        *,
        min_chars: int = 200,
        classified_only: bool = True,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """一行一篇的衛教文章 → 通用知識文件（category=education）。

        篩選：內文 ≥ min_chars、中文比例 ≥ 0.4、（預設）已分類到疾病主題、
        內文不重複（跨醫院常互相轉載）。
        `source` = 原始網址，已匯入過的網址略過，所以可重跑；向量另由 `embed_pending()` 計算。
        """
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以匯入衛教資料")
        seen_hashes = self._documents.existing_hashes()
        seen_sources = {
            d["source"] for d in self._documents.list(hospital_ids=[None]) if d.get("source")
        }
        summary = {"created": 0, "skipped": 0, "duplicates": 0, "read": 0}
        pending_docs: list[dict[str, Any]] = []
        pending_chunks: list[tuple[str, list[dict[str, Any]]]] = []
        for record in _iter_jsonl(path):
            summary["read"] += 1
            if limit is not None and summary["created"] >= limit:
                break
            text = clean_text(record.get("text") or "").strip()
            url = record.get("url") or ""
            if (
                len(text) < min_chars
                or cjk_ratio(text) < 0.4
                or (classified_only and not record.get("topics"))
                or not url
                or url in seen_sources
            ):
                summary["skipped"] += 1
                continue
            digest = content_hash(text)
            if digest in seen_hashes:
                summary["duplicates"] += 1
                continue
            seen_hashes.add(digest)
            seen_sources.add(url)
            doc = self._health_education_document(record, text[:MAX_CONTENT_CHARS], digest, actor)
            pending_docs.append(doc)
            pending_chunks.append((doc["id"], self._chunks_for(doc["title"], doc["content"])))
            summary["created"] += 1
            if len(pending_docs) >= IMPORT_BATCH:
                self._flush(pending_docs, pending_chunks)
                if progress:
                    progress(summary["created"], summary["read"])
        self._flush(pending_docs, pending_chunks)
        return summary

    def _flush(
        self, docs: list[dict[str, Any]], chunks: list[tuple[str, list[dict[str, Any]]]]
    ) -> None:
        if not docs:
            return
        self._documents.create_many(list(docs))
        self._documents.add_chunks_many(list(chunks))
        docs.clear()
        chunks.clear()

    @staticmethod
    def _health_education_document(
        record: dict[str, Any], text: str, digest: str, actor: Principal
    ) -> dict[str, Any]:
        hospital = clean_text(record.get("hospitalName") or "").strip()
        title = clean_text(record.get("title") or "").strip()
        if not title or title == hospital or len(title) < 2:
            title = (record.get("indexTitle") or title or "衛教資料").strip()
        topics = [t for t in record.get("topics") or [] if t]
        forms = [f for f in record.get("forms") or [] if f]
        return {
            "id": uuid.uuid4().hex,
            "title": title[:200],
            "category": "education",
            "hospital_id": None,
            "content": text,
            "tags": (topics + forms)[:20],
            "source": record["url"][:500],
            "content_hash": digest,
            "meta": {
                "external_id": record.get("id"),
                "hospital_code": record.get("hospitalCode"),
                "hospital_name": hospital or None,
                "level": record.get("level"),
                "topics": topics,
                "forms": forms,
                "source_category": record.get("category"),
                "format": record.get("format"),
            },
            "active": True,
            "created_by": actor.user_id,
        }

    # ---- helpers ----

    @staticmethod
    def _chunks_for(title: str, content: str) -> list[dict[str, Any]]:
        return [
            {"content": text, "tokens": tokenize(f"{title} {text}")} for text in chunk_text(content)
        ]

    def _reindex_document(self, doc: dict[str, Any]) -> None:
        self._documents.replace_chunks(doc["id"], self._chunks_for(doc["title"], doc["content"]))

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

    @staticmethod
    def _resolve_hospital(actor: Principal, requested: Any) -> str | None:
        """通用文件只有 SUPER_ADMIN 能建；院方帳號一律綁自己的院所。"""
        hospital_id = str(requested or "").strip() or None
        if actor.role is Role.SUPER_ADMIN:
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


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


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
