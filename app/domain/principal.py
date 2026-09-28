"""Principal：這次請求「是誰」。

後台 API 從 JWT 找出使用者、LINE webhook 從 line_user_id 找出使用者，都包成這個物件；
之後的權限判斷（AccessPolicy、Service、handler）只看它，不碰資料庫也不碰 token。
frozen=True：建好就不能改，避免中途被改掉角色。
"""

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
        """沒登入的人：角色是 GUEST，只能用公開功能。"""
        return cls(user_id=None, role=Role.GUEST)

    @property
    def is_authenticated(self) -> bool:
        """有沒有登入。"""
        return self.user_id is not None

    @property
    def is_verified(self) -> bool:
        """基本資料已填、院方核對通過，可以用個人化功能。"""
        return self.binding_status is BindingStatus.VERIFIED

    @property
    def is_locked(self) -> bool:
        """帳號被院方鎖定，暫停使用。"""
        return self.binding_status is BindingStatus.LOCKED

    @property
    def is_staff(self) -> bool:
        """能不能進後台（院方 + 系統方）。"""
        return self.role.is_staff

    def to_dict(self) -> dict:
        """回給前端用（/api/v1/auth/me 會再加上權限清單）。"""
        return {
            "user_id": self.user_id,
            "role": self.role.value,
            "line_user_id": self.line_user_id,
            "binding_status": self.binding_status.value,
            "hospital_id": self.hospital_id,
            "display_name": self.display_name,
            "is_staff": self.is_staff,
        }
