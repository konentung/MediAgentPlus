"""Principal：目前請求的「身分主體」，由 JWT 或 LINE webhook 解析後掛在 flask.g 上。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import BindingStatus, Role


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    role: Role
    line_user_id: str | None = None
    binding_status: BindingStatus = BindingStatus.UNBOUND
    hospital_id: str | None = None
    display_name: str | None = None

    @classmethod
    def anonymous(cls) -> Principal:
        return cls(user_id=None, role=Role.GUEST)

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def is_verified(self) -> bool:
        """基本資料已填且驗證通過。"""
        return self.binding_status is BindingStatus.VERIFIED

    @property
    def is_locked(self) -> bool:
        return self.binding_status is BindingStatus.LOCKED

    @property
    def is_staff(self) -> bool:
        return self.role.is_staff

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "role": self.role.value,
            "line_user_id": self.line_user_id,
            "binding_status": self.binding_status.value,
            "hospital_id": self.hospital_id,
            "display_name": self.display_name,
        }
