"""角色 → 權限對照表與存取政策。

角色權限以明確列舉定義（非階層繼承），方便稽核每個角色能做什麼。
唯一跟「所屬單位」有關的是單位管理員：同一個角色給醫院 / 診所 / 藥局用，
掛在藥局底下（`Principal.pharmacy_id`）時只拿得到藥局的權限。
"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError
from app.domain.enums import CARE_STAFF_ROLES, PHARMACY_STAFF_ROLES, Role
from app.domain.enums import Permission as P  # noqa: N817
from app.domain.principal import Principal

_PUBLIC: frozenset[P] = frozenset(
    {
        P.CONSULT_SYMPTOM,
        P.CONSULT_HOSPITAL,
        P.CONSULT_MEDICATION,
        P.FEEDBACK_SUBMIT,
        P.INTAKE_WRITE_OWN,
        P.PRESCRIPTION_MANAGE_OWN,
        P.REMINDER_MANAGE_OWN,
    }
)

_BOUND_USER: frozenset[P] = _PUBLIC | {
    P.PROFILE_READ_OWN,
    P.EDUCATION_PERSONAL,
}

# 院方人員同時也是 LINE 使用者：一般諮詢、找醫院等公開功能照樣可用。
# 大醫院分工 1：行政人員（櫃檯 / 個管助理）—— 核對身份建檔、修正病人資料、問題回報
_HOSPITAL_STAFF: frozenset[P] = _PUBLIC | frozenset(
    {
        P.FEEDBACK_MANAGE,
        P.PATIENT_READ,
        P.PATIENT_WRITE,
        P.PATIENT_VERIFY,
    }
)

# 大醫院分工 2：醫護人員（護理師 / 衛教師）—— 就診紀錄、風險與追蹤、衛教文件上傳；不能核對建檔
_MEDICAL_STAFF: frozenset[P] = _PUBLIC | frozenset(
    {
        P.PATIENT_READ,
        P.PATIENT_WRITE,
        P.ENCOUNTER_MANAGE,
        P.EDUCATION_CONTENT_MANAGE,
        P.MEDICATION_MANAGE,
    }
)

# 診所人員：人少不分工，一個角色 = 行政 + 醫護，避免臨時要做某件事卻沒有權限
_CLINIC_STAFF: frozenset[P] = _HOSPITAL_STAFF | _MEDICAL_STAFF

# 單位管理員（醫院 / 診所）：院內全部功能 + 人員 / 院所管理 + 把衛教片段算成向量（會產生費用）
_HOSPITAL_ADMIN: frozenset[P] = _CLINIC_STAFF | {
    P.EDUCATION_EMBED,
    P.STAFF_MANAGE,
    P.HOSPITAL_SETTINGS,
    P.AUDIT_READ,
}

# 藥局人員：處理病人指定到本藥局的領藥需求、維護自己藥局的資料
_PHARMACY_STAFF: frozenset[P] = _PUBLIC | frozenset({P.PHARMACY_SETTINGS, P.PICKUP_MANAGE})

# 單位管理員（藥局）：藥局人員的全部 + 指派本藥局人員。沒有病人 / 就診 / 衛教相關權限
PHARMACY_ADMIN_PERMISSIONS: frozenset[P] = _PHARMACY_STAFF | {P.STAFF_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[P]] = {
    Role.GUEST: _PUBLIC,
    Role.PATIENT: _BOUND_USER,
    Role.FAMILY: _BOUND_USER,
    Role.CLINIC_STAFF: _CLINIC_STAFF,
    Role.HOSPITAL_STAFF: _HOSPITAL_STAFF,
    Role.MEDICAL_STAFF: _MEDICAL_STAFF,
    Role.PHARMACY_STAFF: _PHARMACY_STAFF,
    # 單位管理員掛在院所底下時的權限；
    # 掛在藥局底下時改用 PHARMACY_ADMIN_PERMISSIONS（見 permissions_of）
    Role.HOSPITAL_ADMIN: _HOSPITAL_ADMIN,
    # 系統方：全部權限，所以看得到所有介面；
    # HOSPITAL_MANAGE / REFERENCE_DATA_MANAGE / PUSH_MANAGE / PERMISSION_MATRIX_READ
    # 沒有列在其他角色，只有系統方才有
    Role.SUPER_ADMIN: frozenset(P),
}

# 權限表上「單位管理員（藥局）」那一欄的鍵；不是真的角色，只用來顯示
PHARMACY_ADMIN_KEY = "pharmacy_admin"


def permissions_for(role: Role) -> frozenset[P]:
    """角色本身的權限（不看所屬單位）。判斷某個人能不能做某件事請用 `permissions_of()`。"""
    return ROLE_PERMISSIONS.get(role, frozenset())


def permissions_of(principal: Principal) -> frozenset[P]:
    """這個人實際擁有的權限：單位管理員是三種單位共用的角色，掛在藥局底下時只有藥局的權限。"""
    if principal.role is Role.HOSPITAL_ADMIN and principal.pharmacy_id:
        return PHARMACY_ADMIN_PERMISSIONS
    return permissions_for(principal.role)


def assignable_roles(actor: Principal) -> frozenset[Role]:
    """這個人可以指派哪些角色（避免越權提權）。

    單位管理員只能指派自己單位類型的角色：院所管理員不能指派藥局人員，反之亦然；
    GUEST 讓單位管理員能把離職同仁降回一般使用者；院方不能指派系統方角色。
    """
    if actor.role is Role.SUPER_ADMIN:
        return frozenset(Role)
    if actor.role is not Role.HOSPITAL_ADMIN:
        return frozenset()
    staff_roles = PHARMACY_STAFF_ROLES if actor.pharmacy_id else CARE_STAFF_ROLES
    return frozenset({Role.GUEST, Role.HOSPITAL_ADMIN}) | staff_roles


class AccessPolicy:
    """集中所有授權判斷，Service / Decorator 皆透過此類別做決策。"""

    @staticmethod
    def has_permission(principal: Principal, permission: P) -> bool:
        return permission in permissions_of(principal)

    @staticmethod
    def has_all(principal: Principal, permissions: tuple[P, ...]) -> bool:
        granted = permissions_of(principal)
        return all(p in granted for p in permissions)

    @staticmethod
    def has_any(principal: Principal, permissions: tuple[P, ...]) -> bool:
        granted = permissions_of(principal)
        return any(p in granted for p in permissions)

    @staticmethod
    def can_access_hospital(principal: Principal, hospital_id: str | None) -> bool:
        """院所資料隔離：SUPER_ADMIN 不受限，其餘角色只能存取自己所屬院所。"""
        if principal.role is Role.SUPER_ADMIN:
            return True
        if hospital_id is None:
            return False
        return principal.hospital_id == hospital_id

    @staticmethod
    def can_access_pharmacy(principal: Principal, pharmacy_id: str | None) -> bool:
        """藥局資料隔離：SUPER_ADMIN 不受限，其餘角色只能存取自己所屬藥局。"""
        if principal.role is Role.SUPER_ADMIN:
            return True
        if pharmacy_id is None:
            return False
        return principal.pharmacy_id == pharmacy_id

    @staticmethod
    def can_assign_role(actor: Principal, target_role: Role) -> bool:
        return target_role in assignable_roles(actor)

    @staticmethod
    def hospital_scope(principal: Principal) -> str | None:
        """院方查詢的院所範圍：SUPER_ADMIN 為 None（不限），其他人必須有所屬院所，否則拒絕。

        沒有 hospital_id 的院方帳號直接拒絕，不會退化成不過濾而看到全系統資料。
        """
        if principal.role is Role.SUPER_ADMIN:
            return None
        if not principal.hospital_id:
            raise AuthorizationError("你的帳號尚未指派所屬院所，請聯絡單位管理員")
        return principal.hospital_id

    @staticmethod
    def pharmacy_scope(principal: Principal) -> str | None:
        """藥局查詢的範圍：SUPER_ADMIN 為 None（不限），其他人必須有所屬藥局，否則拒絕。"""
        if principal.role is Role.SUPER_ADMIN:
            return None
        if not principal.pharmacy_id:
            raise AuthorizationError("你的帳號尚未指派所屬藥局，請聯絡單位管理員")
        return principal.pharmacy_id

    @staticmethod
    def matrix() -> dict[str, list[str]]:
        """輸出完整角色權限表（供後台顯示 / 稽核）。

        單位管理員分兩欄：`hospital_admin` 是掛在醫院 / 診所底下時的權限，
        `pharmacy_admin` 是同一個角色掛在藥局底下時的權限。
        """
        matrix: dict[str, list[str]] = {}
        for role, perms in ROLE_PERMISSIONS.items():
            matrix[role.value] = sorted(p.value for p in perms)
            if role is Role.HOSPITAL_ADMIN:
                matrix[PHARMACY_ADMIN_KEY] = sorted(p.value for p in PHARMACY_ADMIN_PERMISSIONS)
        return matrix
