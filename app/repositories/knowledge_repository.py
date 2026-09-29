"""知識庫 Repository：文件 CRUD、片段寫入、關鍵字 / 語意檢索。

PostgreSQL：關鍵字用 tsvector 全文檢索、語意用 pgvector；SQLite（測試）：兩者都在 Python 內計算。
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import defer

from app.domain.retrieval import BM25Index, cosine
from app.extensions import db
from app.models.knowledge import KnowledgeChunkModel, KnowledgeDocumentModel, KnowledgeJobModel
from app.repositories.base import row_to_dict

DOC = KnowledgeDocumentModel
CHUNK = KnowledgeChunkModel
JOB = KnowledgeJobModel


class KnowledgeRepository(Protocol):
    def get(self, document_id: str) -> dict[str, Any] | None: ...
    def list_by_source(self, source: str) -> list[dict[str, Any]]: ...
    def existing_hashes(self) -> set[str]: ...
    def source_index(self) -> dict[str, str]: ...
    def list(
        self,
        *,
        hospital_ids: list[str | None] | None = None,
        active_only: bool = False,
        with_content: bool = True,
    ) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def create_many(self, documents: list[dict[str, Any]]) -> None: ...
    def update(self, document_id: str, **changes: Any) -> dict[str, Any]: ...
    def update_many(self, documents: list[dict[str, Any]]) -> None: ...
    def delete(self, document_id: str) -> None: ...
    def document_ids(self) -> list[str]: ...
    def replace_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> None: ...
    def delete_chunks_many(self, document_ids: list[str]) -> None: ...
    def add_chunks_many(self, items: list[tuple[str, list[dict[str, Any]]]]) -> None: ...
    def search_keyword(
        self,
        query: str,
        tokens: list[str],
        *,
        hospital_ids: list[str | None],
        limit: int,
        department: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def search_vector(
        self,
        vector: list[float],
        *,
        hospital_ids: list[str | None],
        limit: int,
        department: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def pending_embeddings(self, limit: int) -> list[dict[str, Any]]: ...
    def set_embeddings(self, pairs: list[tuple[str, list[float]]]) -> None: ...
    def stats(self) -> dict[str, int]: ...
    def version(self) -> tuple[int, str | None]: ...


def _scope_filter(hospital_ids: list[str | None]):
    """通用（NULL）永遠包含；有指定院所時再加上那些院所。"""
    ids = [h for h in hospital_ids if h]
    condition = DOC.hospital_id.is_(None)
    if ids:
        condition = db.or_(condition, DOC.hospital_id.in_(ids))
    return condition


_HIT_COLUMNS = (
    CHUNK.id,
    CHUNK.document_id,
    CHUNK.content,
    CHUNK.heading,
    CHUNK.kind,
    DOC.title,
    DOC.category,
    DOC.hospital_id,
    DOC.department,
    DOC.source,
    DOC.meta,
)


def _hit(row: Any, score: float) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "content": row.content,
        "heading": row.heading,
        "kind": row.kind,
        "title": row.title,
        "category": row.category,
        "hospital_id": row.hospital_id,
        "department": row.department,
        "source": row.source,
        "meta": row.meta or {},
        "score": round(float(score), 4),
    }


def _department_filter(stmt: Any, department: str | None) -> Any:
    return stmt.where(DOC.department == department) if department else stmt


class SqlAlchemyKnowledgeRepository:
    def __init__(self) -> None:
        self._bm25: BM25Index | None = None
        self._bm25_version: tuple | None = None

    @property
    def _is_postgres(self) -> bool:
        return db.engine.dialect.name == "postgresql"

    # ---- 文件 ----

    def get(self, document_id: str) -> dict[str, Any] | None:
        row = db.session.get(DOC, document_id)
        return row_to_dict(row) if row else None

    def list_by_source(self, source: str) -> list[dict[str, Any]]:
        """同一個 source（檔名）可能有通用一份 + 各院所各一份（修改），
        不能用 scalar_one_or_none。"""
        rows = db.session.execute(
            db.select(DOC).where(DOC.source == source).order_by(DOC.created_at)
        ).scalars()
        return [row_to_dict(r) for r in rows]

    def existing_hashes(self) -> set[str]:
        rows = db.session.execute(
            db.select(DOC.content_hash).where(DOC.content_hash.is_not(None))
        ).scalars()
        return set(rows)

    def source_index(self) -> dict[str, str]:
        """通用文件的 source（網址 / 檔名）→ id，匯入時判斷同網址更新用；只查兩欄。"""  # 新增
        rows = db.session.execute(
            db.select(DOC.source, DOC.id).where(DOC.hospital_id.is_(None), DOC.source.is_not(None))
        )
        return {source: doc_id for source, doc_id in rows}

    def list(
        self,
        *,
        hospital_ids: list[str | None] | None = None,
        active_only: bool = False,
        with_content: bool = True,
    ) -> list[dict[str, Any]]:
        stmt = db.select(DOC).order_by(DOC.category, DOC.title)
        if hospital_ids is not None:
            stmt = stmt.where(_scope_filter(hospital_ids))
        if active_only:
            stmt = stmt.where(DOC.active.is_(True))
        # 修改：後台列表不載入 content（上萬篇內文會有幾十 MB）
        exclude = () if with_content else ("content",)
        if not with_content:
            stmt = stmt.options(defer(DOC.content))
        return [row_to_dict(r, exclude=exclude) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = DOC(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def create_many(self, documents: list[dict[str, Any]]) -> None:
        """批次新增（每筆需自帶 id），一次 commit。"""
        db.session.execute(db.insert(DOC), documents)
        db.session.commit()

    def update(self, document_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(DOC, document_id)
        if row is None:
            raise KeyError(document_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def update_many(self, documents: list[dict[str, Any]]) -> None:
        """批次更新（每筆含 id），一次 commit；匯入時同網址的文件重匯用。"""
        db.session.execute(db.update(DOC), documents)
        db.session.commit()

    def delete(self, document_id: str) -> None:
        row = db.session.get(DOC, document_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()

    def document_ids(self) -> list[str]:
        return list(db.session.execute(db.select(DOC.id).order_by(DOC.created_at)).scalars())

    # ---- 片段 ----

    def _chunk_values(self, document_id: str, seq: int, chunk: dict[str, Any]) -> dict[str, Any]:
        tokens = chunk.get("tokens") or []
        values: dict[str, Any] = {
            "document_id": document_id,
            "seq": seq,
            "content": chunk["content"],
            "heading": (chunk.get("heading") or None),
            "kind": chunk.get("kind") or "text",
            "tokens": tokens,
        }
        if chunk.get("embedding"):
            values["embedding"] = chunk["embedding"]
        if self._is_postgres:
            values["search_vector"] = db.func.to_tsvector("simple", " ".join(tokens))
        return values

    def replace_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> None:
        db.session.execute(db.delete(CHUNK).where(CHUNK.document_id == document_id))
        for seq, chunk in enumerate(chunks):
            db.session.add(CHUNK(**self._chunk_values(document_id, seq, chunk)))
        db.session.commit()

    def delete_chunks_many(self, document_ids: list[str]) -> None:
        if document_ids:
            db.session.execute(db.delete(CHUNK).where(CHUNK.document_id.in_(document_ids)))
            db.session.commit()

    def add_chunks_many(self, items: list[tuple[str, list[dict[str, Any]]]]) -> None:
        for document_id, chunks in items:
            for seq, chunk in enumerate(chunks):
                db.session.add(CHUNK(**self._chunk_values(document_id, seq, chunk)))
        db.session.commit()

    # ---- 檢索 ----

    def search_keyword(
        self,
        query: str,
        tokens: list[str],
        *,
        hospital_ids: list[str | None],
        limit: int,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        """關鍵字檢索；`department` 有值時只在該科別的文件內找（Service 用它做加權排序）。"""
        if not tokens:
            return []
        if self._is_postgres:
            quoted = " | ".join("'" + t.replace("'", "''") + "'" for t in dict.fromkeys(tokens))
            tsquery = db.func.to_tsquery("simple", quoted)
            rank = db.func.ts_rank_cd(CHUNK.search_vector, tsquery)
            stmt = (
                db.select(*_HIT_COLUMNS, rank.label("score"))
                .join(DOC, DOC.id == CHUNK.document_id)
                .where(DOC.active.is_(True), _scope_filter(hospital_ids))
                .where(CHUNK.search_vector.op("@@")(tsquery))
                .order_by(rank.desc())
                .limit(limit)
            )
            stmt = _department_filter(stmt, department)
            return [_hit(row, row.score) for row in db.session.execute(stmt)]
        index, chunks = self._bm25_index(hospital_ids)
        # SQLite：BM25 是全庫索引，科別在結果過濾，所以多取幾倍再截
        top_k = limit * 5 if department else limit
        hits = [
            _hit(chunks[chunk_id], score)
            for chunk_id, score in index.search(query, top_k=top_k)
            if chunk_id in chunks and (not department or chunks[chunk_id].department == department)
        ]
        return hits[:limit]

    def search_vector(
        self,
        vector: list[float],
        *,
        hospital_ids: list[str | None],
        limit: int,
        department: str | None = None,
    ) -> list[dict[str, Any]]:
        if not vector:
            return []
        if self._is_postgres:
            distance = CHUNK.embedding.cosine_distance(vector)
            stmt = (
                db.select(*_HIT_COLUMNS, distance.label("distance"))
                .join(DOC, DOC.id == CHUNK.document_id)
                .where(
                    DOC.active.is_(True), _scope_filter(hospital_ids), CHUNK.embedding.is_not(None)
                )
                .order_by(distance)
                .limit(limit)
            )
            stmt = _department_filter(stmt, department)
            return [_hit(row, 1 - row.distance) for row in db.session.execute(stmt)]
        stmt = (
            db.select(*_HIT_COLUMNS, CHUNK.embedding)
            .join(DOC, DOC.id == CHUNK.document_id)
            .where(DOC.active.is_(True), _scope_filter(hospital_ids), CHUNK.embedding.is_not(None))
        )
        stmt = _department_filter(stmt, department)
        scored = [
            (row, cosine(vector, row.embedding))
            for row in db.session.execute(stmt)
            if row.embedding  # SQLite 的 JSON 欄位把 None 存成 'null'，不會被 IS NOT NULL 濾掉
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [_hit(row, score) for row, score in scored[:limit]]

    def _bm25_index(self, hospital_ids: list[str | None]) -> tuple[BM25Index, dict[str, Any]]:
        """SQLite：所有啟用片段建一份記憶體 BM25，資料版本變了才重建；院所範圍在結果過濾。"""
        version = self.version()
        stmt = (
            db.select(*_HIT_COLUMNS, CHUNK.tokens)
            .join(DOC, DOC.id == CHUNK.document_id)
            .where(DOC.active.is_(True))
        )
        rows = list(db.session.execute(stmt))
        if self._bm25 is None or version != self._bm25_version:
            self._bm25 = BM25Index.build([(r.id, r.tokens or []) for r in rows])
            self._bm25_version = version
        allowed = {h for h in hospital_ids if h}
        chunks = {r.id: r for r in rows if not r.hospital_id or r.hospital_id in allowed}
        return self._bm25, chunks

    # ---- 向量 ----

    def pending_embeddings(self, limit: int) -> list[dict[str, Any]]:
        stmt = (
            db.select(CHUNK.id, CHUNK.content, DOC.title, DOC.department)
            .join(DOC, DOC.id == CHUNK.document_id)
            .where(CHUNK.embedding.is_(None))
            .order_by(CHUNK.document_id, CHUNK.seq)
            .limit(limit)
        )
        return [
            {"id": r.id, "content": r.content, "title": r.title, "department": r.department}
            for r in db.session.execute(stmt)
        ]

    def set_embeddings(self, pairs: list[tuple[str, list[float]]]) -> None:
        for chunk_id, vector in pairs:
            db.session.execute(
                db.update(CHUNK).where(CHUNK.id == chunk_id).values(embedding=vector)
            )
        db.session.commit()

    def stats(self) -> dict[str, int]:
        documents, chunks, embedded = db.session.execute(
            db.select(
                db.select(db.func.count(DOC.id)).scalar_subquery(),
                db.func.count(CHUNK.id),
                db.func.count(CHUNK.embedding),
            )
        ).one()
        return {
            "documents": int(documents or 0),
            "chunks": int(chunks or 0),
            "embedded": int(embedded or 0),
        }

    def version(self) -> tuple[int, str | None]:
        count, latest = db.session.execute(
            db.select(db.func.count(DOC.id), db.func.max(DOC.updated_at))
        ).one()
        return int(count or 0), latest.isoformat() if latest else None


class KnowledgeJobRepository(Protocol):
    def create(self, **values: Any) -> dict[str, Any]: ...
    def get(self, job_id: str) -> dict[str, Any] | None: ...
    def update(self, job_id: str, **changes: Any) -> dict[str, Any]: ...
    def list_recent(self, limit: int) -> list[dict[str, Any]]: ...
    def active(self) -> dict[str, Any] | None: ...


class SqlAlchemyKnowledgeJobRepository:
    """後台維護工作的進度紀錄；背景執行緒與 API 各自用自己的 session 讀寫同一列。"""

    ACTIVE_STATUSES = ("queued", "running")

    def create(self, **values: Any) -> dict[str, Any]:
        row = JOB(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = db.session.get(JOB, job_id)
        return row_to_dict(row) if row else None

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(JOB, job_id)
        if row is None:
            raise KeyError(job_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        stmt = db.select(JOB).order_by(JOB.created_at.desc()).limit(limit)
        return [row_to_dict(r) for r in db.session.scalars(stmt)]

    def active(self) -> dict[str, Any] | None:
        stmt = db.select(JOB).where(JOB.status.in_(self.ACTIVE_STATUSES)).limit(1)
        row = db.session.scalars(stmt).first()
        return row_to_dict(row) if row else None
