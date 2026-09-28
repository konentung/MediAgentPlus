"""使用者意見回饋（取代原本的真人客服工單）。

任何加過好友的人都能從 LIFF 表單送出，不需先填病人表單；後台依院所查看並標記處理。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


class FeedbackModel(db.Model, TimestampMixin):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255))  # 送出當下的 LINE 名稱
    # 病人已確認的就醫院所（沒有就是 NULL，只有 SUPER_ADMIN 看得到）
    hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contact: Mapped[str | None] = mapped_column(String(120))  # 選填：電話 / Email
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolver_note: Mapped[str | None] = mapped_column(Text)
