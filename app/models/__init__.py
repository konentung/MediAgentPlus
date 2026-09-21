"""SQLAlchemy 模型層。

每個模型繼承 `db.Model, TimestampMixin`；模型需在此 import，Flask-Migrate 才能偵測到。
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


from app.models.handoff import HandoffTicketModel  # noqa: E402
from app.models.user import UserModel  # noqa: E402

__all__ = ["TimestampMixin", "UserModel", "HandoffTicketModel"]
