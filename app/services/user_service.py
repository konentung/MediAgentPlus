"""使用者服務：LINE 身分 → 本地使用者、角色指派、Principal 轉換。"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError, NotFoundError
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.user_repository import UserRecord, UserRepository


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    # ---- 查詢 / 建立 ----
    def get_or_create_by_line(
        self, line_user_id: str, display_name: str | None = None, picture_url: str | None = None
    ) -> UserRecord:
        """首次接觸（加入好友 / LINE Login / LIFF）時建立 GUEST 使用者。"""
        user = self._users.get_by_line_user_id(line_user_id)
        if user is None:
            user = UserRecord(
                id=UserRecord.new_id(),
                line_user_id=line_user_id,
                display_name=display_name,
                picture_url=picture_url,
            )
            return self._users.save(user)
        # 同步最新的 LINE 顯示名稱 / 頭像
        if (display_name and display_name != user.display_name) or (
            picture_url and picture_url != user.picture_url
        ):
            user = self._users.update(
                user.id,
                display_name=display_name or user.display_name,
                picture_url=picture_url or user.picture_url,
            )
        return user

    def get(self, user_id: str) -> UserRecord:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("使用者不存在")
        return user

    def get_by_line_user_id(self, line_user_id: str) -> UserRecord | None:
        return self._users.get_by_line_user_id(line_user_id)

    # ---- 權限相關 ----
    def assign_role(
        self, actor: Principal, target_user_id: str, role: Role, hospital_id: str | None = None
    ) -> UserRecord:
        """指派角色；需具 STAFF_MANAGE 且該角色在可指派範圍內，且不得跨院所。"""
        if not AccessPolicy.can_assign_role(actor, role):
            raise AuthorizationError(f"你沒有權限指派角色 {role.value}")
        target = self.get(target_user_id)
        effective_hospital = hospital_id or target.hospital_id or actor.hospital_id
        if role.is_staff and not AccessPolicy.can_access_hospital(actor, effective_hospital):
            raise AuthorizationError("不得指派其他院所的人員")
        return self._users.update(target.id, role=role, hospital_id=effective_hospital)

    # ---- 轉換 ----
    @staticmethod
    def to_principal(user: UserRecord) -> Principal:
        return Principal(
            user_id=user.id,
            role=user.role,
            line_user_id=user.line_user_id,
            binding_status=user.binding_status,
            hospital_id=user.hospital_id,
            display_name=user.display_name,
        )
