"""LIFF 專用端點：登入換 JWT、個人資料。

前端流程：
  liff.init({liffId}) → liff.getIDToken() → POST /liff/auth → 取得 JWT
  之後所有請求帶 Authorization: Bearer <access_token>

綁定流程（B1–B9）尚未設計，相關端點待定案後再加。
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
from app.core.responses import ok
from app.domain.enums import Permission

liff_bp = Blueprint("liff", __name__, url_prefix="/liff")


@liff_bp.get("/config")
def liff_config():
    """前端初始化所需的公開設定。"""
    return ok({"liff_id": get_container().liff_auth.liff_id})


@liff_bp.post("/auth")
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
    return ok(
        {"tokens": container.jwt.issue_pair(principal).to_dict(), "principal": principal.to_dict()}
    )


@liff_bp.get("/me")
@require_auth()
def liff_me():
    return ok({"principal": current_principal().to_dict()})


@liff_bp.get("/profile")
@require_verified()
@require_permission(Permission.PROFILE_READ_OWN)
def profile():
    # TODO: 綁定 / 基本資料模型設計完成後改由 ProfileService 讀取
    return ok({"profile": {}})
