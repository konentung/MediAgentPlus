"""LIFF 專用端點：登入換 JWT、個人資料、綁定流程 B1–B9、解除綁定。

前端流程：
  liff.init({liffId}) → liff.getIDToken() → POST /liff/auth → 取得 JWT
  之後所有請求帶 Authorization: Bearer <access_token>
"""

from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, request

from app.auth.decorators import (
    current_principal,
    require_auth,
    require_permission,
    require_verified,
)
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.responses import ok
from app.domain.enums import IdentityType, Permission, VerificationMethod

liff_bp = Blueprint("liff", __name__, url_prefix="/liff")


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _enum(cls, value: str | None, field: str):
    try:
        return cls(value)
    except ValueError as exc:
        raise ValidationError(
            f"{field} 不合法", details={"allowed": [m.value for m in cls]}
        ) from exc


@liff_bp.get("/config")
def liff_config():
    """前端初始化所需的公開設定。"""
    return ok({"liff_id": get_container().liff_auth.liff_id})


@liff_bp.post("/auth")
def liff_auth():
    body = _json()
    container = get_container()
    identity = container.liff_auth.authenticate(
        id_token=body.get("id_token"), access_token=body.get("access_token")
    )
    user = container.users.get_or_create_by_line(
        identity.line_user_id, identity.display_name, identity.picture_url
    )
    principal = container.users.to_principal(user)
    return ok(
        {"tokens": container.jwt.issue_pair(principal).to_dict(), "principal": principal.to_dict()}
    )


@liff_bp.get("/me")
@require_auth()
def liff_me():
    principal = current_principal()
    session = get_container().binding.current(principal)
    return ok({"principal": principal.to_dict(), "binding": _session_dict(session)})


# ---------------- 綁定流程 ----------------
@liff_bp.post("/binding/start")  # B1
@require_permission(Permission.BINDING_CREATE)
def binding_start():
    session = get_container().binding.start(current_principal())
    return ok(_session_dict(session))


@liff_bp.post("/binding/identity")  # B2
@require_permission(Permission.BINDING_CREATE)
def binding_identity():
    body = _json()
    identity_type = _enum(IdentityType, body.get("identity_type"), "identity_type")
    session = get_container().binding.submit_identity(
        current_principal(), identity_type, body.get("relation")
    )
    return ok(_session_dict(session))


@liff_bp.post("/binding/profile")  # B3
@require_permission(Permission.BINDING_CREATE)
def binding_profile():
    session = get_container().binding.submit_profile(current_principal(), _json())
    return ok(_session_dict(session))


@liff_bp.post("/binding/verification")  # B4
@require_permission(Permission.BINDING_CREATE)
def binding_verification():
    body = _json()
    method = _enum(VerificationMethod, body.get("method"), "method")
    session = get_container().binding.request_verification(
        current_principal(), method, discharge_code=body.get("discharge_code")
    )
    return ok(_session_dict(session))


@liff_bp.post("/binding/consent")  # B5
@require_permission(Permission.BINDING_CREATE)
def binding_consent():
    session = get_container().binding.give_consent(
        current_principal(), bool(_json().get("accepted"))
    )
    return ok(_session_dict(session))


@liff_bp.post("/binding/verify")  # 比對 → B7 / B8 / B9
@require_permission(Permission.BINDING_CREATE)
def binding_verify():
    code = _json().get("code")
    if not code:
        raise ValidationError("缺少驗證碼")
    result = get_container().binding.verify(current_principal(), str(code))
    return ok(
        {
            "outcome": result.outcome.value,
            "failure_count": result.failure_count,
            "message": result.message,
        }
    )


@liff_bp.delete("/binding")  # 設定頁：解除綁定
@require_permission(Permission.BINDING_REVOKE)
def binding_revoke():
    get_container().binding.revoke(current_principal())
    return ok({"revoked": True})


@liff_bp.get("/profile")
@require_verified()
@require_permission(Permission.PROFILE_READ_OWN)
def profile():
    # TODO: 模型設計完成後改由 ProfileService 讀取
    session = get_container().binding.current(current_principal())
    return ok({"profile": session.profile if session else {}})


def _session_dict(session) -> dict | None:
    if session is None:
        return None
    data = asdict(session)
    data.pop("verification_code", None)  # 絕不回傳驗證碼
    for key in ("step", "identity_type", "verification_method"):
        if data.get(key) is not None:
            data[key] = data[key].value
    data["updated_at"] = session.updated_at.isoformat()
    return data
