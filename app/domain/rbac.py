"""角色 → 權限對照表與存取政策。

刻意使用「明確列舉」而非階層繼承，方便稽核每個角色到底能做什麼。
"""

from __future__ import annotations

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
    }
)

_BOUND_USER: frozenset[P] = _PUBLIC | {
    P.PROFILE_READ_OWN,
    P.PROFILE_WRITE_OWN,
    P.EDUCATION_PERSONAL,
    P.REMINDER_MANAGE_OWN,
}

_CS_AGENT: frozenset[P] = frozenset(
    {
        P.HANDOFF_ACCEPT,
        P.HANDOFF_REPLY,
        P.CONVERSATION_READ,
        P.PATIENT_READ,
    }
)

_MEDICAL_STAFF: frozenset[P] = frozenset(
    {
        P.PATIENT_READ,
        P.CONVERSATION_READ,
        P.EDUCATION_CONTENT_MANAGE,
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
    Role.HOSPITAL_ADMIN: frozenset({Role.CS_AGENT, Role.MEDICAL_STAFF, Role.HOSPITAL_ADMIN}),
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
    def matrix() -> dict[str, list[str]]:
        """輸出完整角色權限矩陣（供後台顯示 / 稽核）。"""
        return {
            role.value: sorted(p.value for p in perms) for role, perms in ROLE_PERMISSIONS.items()
        }
