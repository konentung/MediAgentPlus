"""知識庫（RAG 檢索來源）：文件與斷詞後的片段。

`hospital_id` 為 NULL 代表全系統通用，否則只對該院所的病人檢索得到。
片段由 `KnowledgeService` 依 `app/domain/retrieval.py` 切段與斷詞後寫入：
- `tokens`：jieba 斷詞結果（SQLite 測試環境的記憶體 BM25 用）
- `search_vector`：同一組 tokens 的 tsvector（PostgreSQL 全文檢索，GIN 索引）
- `embedding`：OpenAI 向量（PostgreSQL 用 pgvector；SQLite 存 JSON 供測試）
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small

JsonColumn = JSONB().with_variant(JSON(), "sqlite")
SearchVectorColumn = TSVECTOR().with_variant(Text(), "sqlite")
# SQLite 的 JSON 預設把 None 存成 'null' 字串，none_as_null 讓它變成真正的 NULL
EmbeddingColumn = Vector(EMBEDDING_DIMENSIONS).with_variant(JSON(none_as_null=True), "sqlite")


def _new_id() -> str:
    return uuid.uuid4().hex


class KnowledgeDocumentModel(db.Model, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="faq", nullable=False)
    hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JsonColumn)
    source: Mapped[str | None] = mapped_column(String(500), index=True)  # 檔名 / 網址，匯入時去重
    content_hash: Mapped[str | None] = mapped_column(String(40), index=True)  # 內文 SHA-1，去重
    meta: Mapped[dict | None] = mapped_column(JsonColumn)  # 出處：醫院、層級、主題、格式
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32))


class KnowledgeChunkModel(db.Model, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    document_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[list | None] = mapped_column(JsonColumn)
    search_vector: Mapped[object | None] = mapped_column(SearchVectorColumn)
    embedding: Mapped[object | None] = mapped_column(EmbeddingColumn)
