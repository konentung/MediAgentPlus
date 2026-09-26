"""登入工作階段：每一個 refresh token 對應一列，支援輪替、重用偵測與撤銷。

`id` 就是 refresh token 的 `jti`；同一次登入輪替出來的 token 共用 `family_id`，
偵測到舊 token 被重用時整個 family 一起撤銷。網頁版 LINE Login 會把 LINE 自己的
access / refresh token 加密存在這裡，refresh 時同步向 LINE 換新、登出時向 LINE 撤銷。
"""

from __future__ import annotations

import uuid

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


class AuthSessionModel(db.Model, TimestampMixin):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    family_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # line_login / liff
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[str | None] = mapped_column(String(32))
    line_access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    line_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    line_token_expires_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))
