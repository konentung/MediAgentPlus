"""使用者資料表模型，對應 `app.repositories.user_repository.UserRecord`。"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import BindingStatus, Role
from app.extensions import db
from app.models import TimestampMixin


class UserModel(db.Model, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    line_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    picture_url: Mapped[str | None] = mapped_column(String(512))
    # role 不加 CHECK constraint：角色會持續新增，由 Role 列舉驗證，
    # 新增角色只改 enums.py + rbac.py，不用寫 migration。binding_status 值域固定，保留 CHECK。
    role: Mapped[Role] = mapped_column(
        db.Enum(Role, name="role", native_enum=False, length=32, create_constraint=False),
        default=Role.GUEST,
        nullable=False,
    )
    binding_status: Mapped[BindingStatus] = mapped_column(
        db.Enum(
            BindingStatus,
            name="binding_status",
            native_enum=False,
            length=16,
            create_constraint=True,
        ),
        default=BindingStatus.UNBOUND,
        nullable=False,
    )
    hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    # 使用者封鎖官方帳號後設為 False，所有主動推播（服藥提醒等）都要略過
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
