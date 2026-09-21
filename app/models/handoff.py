"""真人客服工單資料表模型，對應 `app.repositories.handoff_repository.HandoffTicket`。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


class HandoffTicketModel(db.Model, TimestampMixin):
    __tablename__ = "handoff_tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(32))
    hospital_id: Mapped[str | None] = mapped_column(String(32), index=True)
