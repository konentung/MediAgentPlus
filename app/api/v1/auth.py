"""LINE Login（網頁端 OAuth）與自家 JWT 端點。

GET  /api/v1/auth/line/login?redirect_to=...  → 302 導向 LINE 授權頁
GET  /api/v1/auth/line/callback?code&state     → 換發 JWT（有 FRONTEND_URL 時改 302 帶 token）
POST /api/v1/auth/refresh                      → 以 refresh_token 換新 access_token
GET  /api/v1/auth/me                           → 目前身分與權限
"""

from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, current_app, redirect, request

from app.auth.decorators import current_principal, require_auth
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.responses import ok
from app.domain.rbac import permissions_for

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.get("/line/login")
def line_login():
    url = get_container().line_login.build_authorize_url(
        redirect_to=request.args.get("redirect_to")
    )
    return redirect(url, code=302)


@auth_bp.get("/line/callback")
def line_callback():
    container = get_container()
    result = container.line_login.handle_callback(
        code=request.args.get("code"),
        state=request.args.get("state"),
        error=request.args.get("error"),
    )
    identity = result.identity
    user = container.users.get_or_create_by_line(
        identity.line_user_id, identity.display_name, identity.picture_url
    )
    tokens = container.jwt.issue_pair(container.users.to_principal(user))

    frontend = current_app.config.get("FRONTEND_URL")
    if frontend:
        target = result.redirect_to or f"{frontend.rstrip('/')}/auth/callback"
        return redirect(f"{target}#{urlencode(tokens.to_dict())}", code=302)
    return ok(tokens.to_dict())


@auth_bp.post("/refresh")
def refresh():
    body = request.get_json(silent=True) or {}
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise ValidationError("缺少 refresh_token")
    container = get_container()
    claims = container.jwt.decode(refresh_token, expected_type="refresh")
    user = container.users.get(claims["sub"])
    return ok(container.jwt.issue_pair(container.users.to_principal(user)).to_dict())


@auth_bp.get("/me")
@require_auth()
def me():
    principal = current_principal()
    return ok(
        {
            **principal.to_dict(),
            "permissions": sorted(p.value for p in permissions_for(principal.role)),
        }
    )
