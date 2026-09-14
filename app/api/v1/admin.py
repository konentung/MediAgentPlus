"""院方後台端點（真人客服 / 醫護 / 院所管理員 / 系統管理員）。

此處只放骨架與權限閘門，具體業務邏輯待模型設計完成後補上。
"""

from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, request

from app.auth.decorators import current_principal, require_permission, require_role
from app.core.container import get_container
from app.core.exceptions import AuthorizationError, ValidationError
from app.core.responses import ok
from app.domain.enums import Permission, Role
from app.domain.rbac import AccessPolicy
from app.repositories.binding_repository import RosterEntry

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/permissions")
@require_role(*[r for r in Role if r.is_staff])
def permission_matrix():
    return ok(AccessPolicy.matrix())


# ---- 人員管理 ----
@admin_bp.put("/users/<user_id>/role")
@require_permission(Permission.STAFF_MANAGE)
def set_role(user_id: str):
    body = request.get_json(silent=True) or {}
    try:
        role = Role(body.get("role"))
    except ValueError as exc:
        raise ValidationError("role 不合法", details={"allowed": [r.value for r in Role]}) from exc
    user = get_container().users.assign_role(
        current_principal(), user_id, role, body.get("hospital_id")
    )
    return ok(get_container().users.to_principal(user).to_dict())


# ---- 院方識別名單 ----
@admin_bp.get("/roster")
@require_permission(Permission.ROSTER_READ)
def list_roster():
    actor = current_principal()
    hospital_id = request.args.get("hospital_id") or actor.hospital_id
    if not AccessPolicy.can_access_hospital(actor, hospital_id):
        raise AuthorizationError("不得存取其他院所的識別名單")
    entries = get_container().roster_repo.list_by_hospital(hospital_id or "")
    return ok([asdict(e) for e in entries])


@admin_bp.post("/roster")
@require_permission(Permission.ROSTER_MANAGE)
def upsert_roster():
    actor = current_principal()
    body = request.get_json(silent=True) or {}
    hospital_id = body.get("hospital_id") or actor.hospital_id
    if not AccessPolicy.can_access_hospital(actor, hospital_id):
        raise AuthorizationError("不得維護其他院所的識別名單")
    required = ("id", "name", "birth_date", "phone_last4")
    missing = [f for f in required if not body.get(f)]
    if missing:
        raise ValidationError("缺少必填欄位", details={"missing": missing})
    entry = RosterEntry(
        id=body["id"],
        hospital_id=hospital_id,
        name=body["name"],
        birth_date=body["birth_date"],
        phone_last4=str(body["phone_last4"]),
        department=body.get("department"),
        discharge_code=body.get("discharge_code"),
    )
    return ok(asdict(get_container().roster_repo.save(entry)), status=201)


# ---- 真人客服工單 ----
@admin_bp.get("/handoffs")
@require_permission(Permission.HANDOFF_ACCEPT)
def list_handoffs():
    tickets = get_container().handoff.list_open(current_principal())
    return ok([asdict(t) for t in tickets])


@admin_bp.post("/handoffs/<ticket_id>/accept")
@require_permission(Permission.HANDOFF_ACCEPT)
def accept_handoff(ticket_id: str):
    ticket = get_container().handoff.accept(current_principal(), ticket_id)
    return ok(asdict(ticket))
