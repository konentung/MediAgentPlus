"""稽核紀錄：誰在什麼時候對哪位病人做了高敏感操作（例如讀取身分證明碼、鎖定 / 解鎖帳號）。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin
from app.models.patient import JsonColumn


class AuditLogModel(db.Model, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    actor_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    target_user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    detail: Mapped[dict | None] = mapped_column(JsonColumn)
