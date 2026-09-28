"""角色 → 權限對照表與存取政策。

刻意使用「明確列舉」而非階層繼承，方便稽核每個角色到底能做什麼。
"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError
from app.domain.enums import Permission as P  # noqa: N817
from app.domain.enums import Role
from app.domain.principal import Principal

_PUBLIC: frozenset[P] = frozenset(
    {
        P.CONSULT_SYMPTOM,
        P.CONSULT_HOSPITAL,
        P.CONSULT_MEDICATION_ONCE,
        P.CONSULT_EDUCATION_GENERIC,
        P.HANDOFF_REQUEST,
        P.INTAKE_WRITE_OWN,
        P.PRESCRIPTION_MANAGE_OWN,
        P.REMINDER_MANAGE_OWN,
    }
)

_BOUND_USER: frozenset[P] = _PUBLIC | {
    P.PROFILE_READ_OWN,
    P.PROFILE_WRITE_OWN,
    P.EDUCATION_PERSONAL,
}

# 院方人員同時也是 LINE 使用者：一般諮詢、找醫院等公開功能照樣可用
_CS_AGENT: frozenset[P] = _PUBLIC | frozenset(
    {
        P.HANDOFF_ACCEPT,
        P.HANDOFF_REPLY,
        P.CONVERSATION_READ,
        P.PATIENT_READ,
        P.TRIAGE_READ,
    }
)

_MEDICAL_STAFF: frozenset[P] = _PUBLIC | frozenset(
    {
        P.PATIENT_READ,
        P.CONVERSATION_READ,
        P.EDUCATION_CONTENT_MANAGE,
        P.PATIENT_WRITE,
        P.PATIENT_VERIFY,
        P.ENCOUNTER_MANAGE,
        P.TRIAGE_READ,
    }
)

_HOSPITAL_ADMIN: frozenset[P] = (
    _CS_AGENT
    | _MEDICAL_STAFF
    | {
        P.STAFF_MANAGE,
        P.HOSPITAL_SETTINGS,
        P.RICHMENU_MANAGE,
        P.AUDIT_READ,
        P.PATIENT_IDENTITY_READ,
        P.TRIAGE_SUBSCRIPTION_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[P]] = {
    Role.GUEST: _PUBLIC,
    Role.PATIENT: _BOUND_USER,
    Role.FAMILY: _BOUND_USER,
    Role.CS_AGENT: _CS_AGENT,
    Role.MEDICAL_STAFF: _MEDICAL_STAFF,
    Role.HOSPITAL_ADMIN: _HOSPITAL_ADMIN,
    Role.SUPER_ADMIN: frozenset(P),
}

# 哪些角色可以指派哪些角色（避免越權提權）
ROLE_ASSIGNABLE_BY: dict[Role, frozenset[Role]] = {
    Role.SUPER_ADMIN: frozenset(Role),
    # GUEST 讓院所管理員能把離職同仁降回一般使用者
    Role.HOSPITAL_ADMIN: frozenset(
        {Role.GUEST, Role.CS_AGENT, Role.MEDICAL_STAFF, Role.HOSPITAL_ADMIN}
    ),
}


def permissions_for(role: Role) -> frozenset[P]:
    return ROLE_PERMISSIONS.get(role, frozenset())


class AccessPolicy:
    """集中所有授權判斷，Service / Decorator 皆透過此類別做決策。"""

    @staticmethod
    def has_permission(principal: Principal, permission: P) -> bool:
        return permission in permissions_for(principal.role)

    @staticmethod
    def has_all(principal: Principal, permissions: tuple[P, ...]) -> bool:
        granted = permissions_for(principal.role)
        return all(p in granted for p in permissions)

    @staticmethod
    def has_any(principal: Principal, permissions: tuple[P, ...]) -> bool:
        granted = permissions_for(principal.role)
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
    def can_assign_role(actor: Principal, target_role: Role) -> bool:
        return target_role in ROLE_ASSIGNABLE_BY.get(actor.role, frozenset())

    @staticmethod
    def hospital_scope(principal: Principal) -> str | None:
        """院方查詢的院所範圍：SUPER_ADMIN 為 None（不限），其他人必須有所屬院所，否則拒絕。

        刻意不讓「沒有 hospital_id 的院方帳號」退化成不過濾，避免看到全系統資料。
        """
        if principal.role is Role.SUPER_ADMIN:
            return None
        if not principal.hospital_id:
            raise AuthorizationError("你的帳號尚未指派所屬院所，請聯絡管理員")
        return principal.hospital_id

    @staticmethod
    def matrix() -> dict[str, list[str]]:
        """輸出完整角色權限矩陣（供後台顯示 / 稽核）。"""
        return {
            role.value: sorted(p.value for p in perms) for role, perms in ROLE_PERMISSIONS.items()
        }
