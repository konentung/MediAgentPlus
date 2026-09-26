"""LIFF 專用端點：登入換 JWT、病人自填表（intake）、個人資料。

前端流程：
  liff.init({liffId}) → liff.getIDToken() → POST /api/v1/liff/auth → 取得 JWT
  之後所有請求帶 Authorization: Bearer <access_token>

表單流程（頁面在 /liff/intake，見 app/liff_pages.py）：
  GET  /api/v1/liff/intake              schema + 已填資料 + 進度
  PUT  /api/v1/liff/intake/steps/<n>    存單一步驟（body 帶 partial=true 為草稿）
  POST /api/v1/liff/intake/submit       全表驗證 → 快照 → binding_status=PENDING
"""

from __future__ import annotations

from flask import Blueprint, request

from app.auth.decorators import (
    current_principal,
    require_auth,
    require_permission,
    require_verified,
)
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission

liff_bp = Blueprint("liff", __name__, url_prefix="/liff")


@liff_bp.get("/config")
def liff_config():
    """前端初始化所需的公開設定。"""
    return ok({"liff_id": get_container().liff_auth.liff_id})


@liff_bp.post("/auth")
@rate_limit("liff_auth", limit=30, per_seconds=300)
def liff_auth():
    body = request.get_json(silent=True) or {}
    container = get_container()
    identity = container.liff_auth.authenticate(
        id_token=body.get("id_token"), access_token=body.get("access_token")
    )
    user = container.users.get_or_create_by_line(
        identity.line_user_id, identity.display_name, identity.picture_url
    )
    principal = container.users.to_principal(user)
    tokens = container.auth_sessions.start(
        principal,
        provider="liff",
        user_agent=request.user_agent.string,
        ip_address=request.remote_addr,
    )
    return ok({"tokens": tokens.to_dict(), "principal": principal.to_dict()})


@liff_bp.get("/me")
@require_auth()
def liff_me():
    return ok({"principal": current_principal().to_dict()})


@liff_bp.get("/hospitals")
def liff_hospitals():
    """表單「常去的醫院 / 診所」選單，公開。"""
    return ok(get_container().hospitals.public_list())


@liff_bp.get("/intake")
@require_permission(Permission.INTAKE_WRITE_OWN)
def intake_state():
    return ok(get_container().intake.get_form_state(current_principal()))


@liff_bp.put("/intake/steps/<int:step_number>")
@require_permission(Permission.INTAKE_WRITE_OWN)
@rate_limit("intake_save", limit=120, per_seconds=600, by="user")
def intake_save_step(step_number: int):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出表單資料")
    partial = bool(body.pop("partial", False))
    profile = get_container().intake.save_step(
        current_principal(),
        step_number,
        body,
        partial=partial,
        ip_address=request.remote_addr,
    )
    return ok({"profile": profile})


@liff_bp.post("/intake/submit")
@require_permission(Permission.INTAKE_WRITE_OWN)
def intake_submit():
    return ok(get_container().intake.submit(current_principal()))


@liff_bp.get("/profile")
@require_verified()
@require_permission(Permission.PROFILE_READ_OWN)
def profile():
    return ok({"profile": get_container().intake.get_profile(current_principal())})
