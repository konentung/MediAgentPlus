"""使用者服務：LINE 身分 → 本地使用者、角色指派、Principal 轉換。"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.domain.enums import CARE_STAFF_ROLES, PHARMACY_STAFF_ROLES, SYSTEM_ROLES, Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.pharmacy_repository import PharmacyRepository
from app.repositories.user_repository import UserRecord, UserRepository
from app.services.hospital_service import HospitalService


class UserService:
    def __init__(
        self,
        users: UserRepository,
        hospitals: HospitalService,
        super_admin_line_user_ids: frozenset[str] = frozenset(),
        pharmacies: PharmacyRepository | None = None,
    ) -> None:
        self._users = users
        self._hospitals = hospitals
        self._pharmacies = pharmacies
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
            user = self._users.update(
                user.id, role=Role.SUPER_ADMIN, hospital_id=None, pharmacy_id=None
            )
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
        pharmacy_id: str | None = None,
        clear_hospital: bool = False,
    ) -> UserRecord:
        """指派角色。

        - 目標的新角色與現任角色都必須在 actor 可指派範圍內（避免單位管理員降級其他單位的管理員）。
        - 不能改自己的角色。
        - 院方角色必須有所屬單位，且不得跨單位：醫院 / 診所的角色掛院所、藥局人員掛藥局、
          單位管理員兩者擇一；系統方角色（SUPER_ADMIN）不屬於任何單位。
        - `clear_hospital=True` 才會把院所清空；未提供單位時沿用目標原本的，再沒有就用 actor 的。
        """
        if actor.user_id == target_user_id:
            raise AuthorizationError("不能修改自己的角色")
        target = self.get(target_user_id)
        if not AccessPolicy.can_assign_role(actor, role) or not AccessPolicy.can_assign_role(
            actor, target.role
        ):
            raise AuthorizationError(f"你沒有權限指派角色 {role.value}")
        if target.role.is_hospital_staff and not self._same_org(actor, target):
            raise AuthorizationError("不得修改其他院所或藥局人員的角色")

        hospital, pharmacy = self._resolve_org(
            actor, target, role, hospital_id, pharmacy_id, clear_hospital
        )
        if hospital:
            if role.is_hospital_staff and not AccessPolicy.can_access_hospital(actor, hospital):
                raise AuthorizationError("不得指派其他院所的人員")
            self._hospitals.require_active(hospital)
        if pharmacy:
            if not AccessPolicy.can_access_pharmacy(actor, pharmacy):
                raise AuthorizationError("不得指派其他藥局的人員")
            self._require_active_pharmacy(pharmacy)
        return self._users.update(target.id, role=role, hospital_id=hospital, pharmacy_id=pharmacy)

    @staticmethod
    def _same_org(actor: Principal, target: UserRecord) -> bool:
        if target.pharmacy_id:
            return AccessPolicy.can_access_pharmacy(actor, target.pharmacy_id)
        return AccessPolicy.can_access_hospital(actor, target.hospital_id)

    @staticmethod
    def _resolve_org(
        actor: Principal,
        target: UserRecord,
        role: Role,
        hospital_id: str | None,
        pharmacy_id: str | None,
        clear_hospital: bool,
    ) -> tuple[str | None, str | None]:
        """新角色該掛在哪個單位，回傳 (hospital_id, pharmacy_id)，院方角色兩者恰好一個有值。"""
        if role in SYSTEM_ROLES:
            # 系統方角色不屬於任何單位；留著會出現在該單位的人員清單裡
            return None, None
        inherited_hospital = None if clear_hospital else target.hospital_id or actor.hospital_id
        if role in PHARMACY_STAFF_ROLES:
            pharmacy = pharmacy_id or target.pharmacy_id or actor.pharmacy_id
            if not pharmacy:
                raise ValidationError("藥局人員必須指定所屬藥局")
            return None, pharmacy
        if role in CARE_STAFF_ROLES:
            hospital = hospital_id or inherited_hospital
            if not hospital:
                raise ValidationError("院方人員必須指定所屬院所")
            return hospital, None
        if role is Role.HOSPITAL_ADMIN:
            # 單位管理員：有明確指定就照指定（藥局優先），否則沿用目標原本的單位，
            # 再沒有就跟 actor 同單位
            if pharmacy_id:
                return None, pharmacy_id
            if hospital_id:
                return hospital_id, None
            if target.pharmacy_id or (not inherited_hospital and actor.pharmacy_id):
                return None, target.pharmacy_id or actor.pharmacy_id
            if not inherited_hospital:
                raise ValidationError("單位管理員必須指定所屬院所或藥局")
            return inherited_hospital, None
        # 一般使用者：不屬於藥局；院所沿用原本的行為（降回一般使用者時保留原院所）
        return hospital_id or inherited_hospital, None

    def _require_active_pharmacy(self, pharmacy_id: str) -> None:
        pharmacy = self._pharmacies.get(pharmacy_id) if self._pharmacies else None
        if pharmacy is None or not pharmacy.get("active", True):
            raise ValidationError("找不到這家藥局，或藥局已停用")

    @staticmethod
    def to_principal(user: UserRecord) -> Principal:
        return Principal(
            user_id=user.id,
            role=user.role,
            line_user_id=user.line_user_id,
            binding_status=user.binding_status,
            hospital_id=user.hospital_id,
            pharmacy_id=user.pharmacy_id,
            display_name=user.display_name,
        )
