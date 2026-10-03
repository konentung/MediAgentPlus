"""使用者服務：LINE 身分 → 本地使用者、角色指派、Principal 轉換。"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.domain.enums import SYSTEM_ROLES, Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.user_repository import UserRecord, UserRepository
from app.services.hospital_service import HospitalService


class UserService:
    def __init__(
        self,
        users: UserRepository,
        hospitals: HospitalService,
        super_admin_line_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._users = users
        self._hospitals = hospitals
        # 設定檔 SUPER_ADMIN_LINE_USER_IDS：沒有任何管理員時的唯一入口，其餘角色都在後台指派
        self._super_admins = super_admin_line_user_ids

    def get_or_create_by_line(
        self, line_user_id: str, display_name: str | None = None, picture_url: str | None = None
    ) -> UserRecord:
        """首次接觸（加入好友 / LINE Login / LIFF）時建立 GUEST 使用者，並同步 LINE 名稱 / 頭像。"""
        user = self._users.get_by_line_user_id(line_user_id)
        if user is None:
            user = self._users.save(
                UserRecord(
                    id=UserRecord.new_id(),
                    line_user_id=line_user_id,
                    display_name=display_name,
                    picture_url=picture_url,
                )
            )
        elif (display_name and display_name != user.display_name) or (
            picture_url and picture_url != user.picture_url
        ):
            user = self._users.update(
                user.id,
                display_name=display_name or user.display_name,
                picture_url=picture_url or user.picture_url,
            )
        if line_user_id in self._super_admins and user.role is not Role.SUPER_ADMIN:
            user = self._users.update(user.id, role=Role.SUPER_ADMIN, hospital_id=None)
        return user

    def get(self, user_id: str) -> UserRecord:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("使用者不存在")
        return user

    def get_by_line_user_id(self, line_user_id: str) -> UserRecord | None:
        return self._users.get_by_line_user_id(line_user_id)

    def assign_role(
        self,
        actor: Principal,
        target_user_id: str,
        role: Role,
        hospital_id: str | None = None,
        *,
        clear_hospital: bool = False,
    ) -> UserRecord:
        """指派角色。

        - 目標的新角色與現任角色都必須在 actor 可指派範圍內（避免院所管理員降級其他管理員）。
        - 不能改自己的角色。
        - 院方角色必須有所屬院所，且不得跨院所；系統方角色（SUPER_ADMIN）不屬於任何院所。
        - `clear_hospital=True` 才會把院所清空；未提供 hospital_id 時沿用目標原本的院所。
        """
        if actor.user_id == target_user_id:
            raise AuthorizationError("不能修改自己的角色")
        target = self.get(target_user_id)
        if not AccessPolicy.can_assign_role(actor, role) or not AccessPolicy.can_assign_role(
            actor, target.role
        ):
            raise AuthorizationError(f"你沒有權限指派角色 {role.value}")
        if target.role.is_hospital_staff and not AccessPolicy.can_access_hospital(
            actor, target.hospital_id
        ):
            raise AuthorizationError("不得修改其他院所人員的角色")

        if clear_hospital or role in SYSTEM_ROLES:
            # 系統方角色不屬於任何院所；留著院所會出現在該院所的人員清單裡
            effective_hospital = None
        else:
            effective_hospital = hospital_id or target.hospital_id or actor.hospital_id
        if role.is_hospital_staff:
            if not effective_hospital:
                raise ValidationError("院方人員必須指定所屬院所")
            if not AccessPolicy.can_access_hospital(actor, effective_hospital):
                raise AuthorizationError("不得指派其他院所的人員")
        if effective_hospital:
            self._hospitals.require_active(effective_hospital)
        return self._users.update(target.id, role=role, hospital_id=effective_hospital)

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
