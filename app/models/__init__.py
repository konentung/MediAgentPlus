"""SQLAlchemy 模型層。

資料表模型尚未設計；此處僅提供共用的 Mixin，日後每個模型繼承 `db.Model, TimestampMixin` 即可。
Repository 層目前使用 in-memory 實作，換成 SQLAlchemy 實作時介面（Protocol）不變。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
