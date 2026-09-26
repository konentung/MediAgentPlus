"""LINE Login（網頁端 OAuth）與自家 JWT 端點。

GET  /api/v1/auth/line/login?redirect_to=...  → 302 導向 LINE 授權頁
GET  /api/v1/auth/line/callback?code&state     → 換發 JWT，render 登入成功頁（Jinja）
POST /api/v1/auth/refresh                      → 輪替 refresh token（同步向 LINE 換新 token）
POST /api/v1/auth/logout                       → 撤銷本次登入（並向 LINE 撤銷 token）
GET  /api/v1/auth/me                           → 目前身分與權限
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from flask import Blueprint, redirect, render_template, request

from app.auth.decorators import current_principal, require_auth
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.rbac import permissions_for

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

DEFAULT_REDIRECT_TO = "/"


def _safe_redirect_to(value: str | None) -> str:
    """登入後只允許導向站內相對路徑，避免 open redirect；未指定則回首頁。

    瀏覽器會把 `/\evil.com` 的反斜線當成斜線，所以除了 `//` 也要擋 `\`，
    並用 urlsplit 確認沒有 scheme / netloc。
    """
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return DEFAULT_REDIRECT_TO
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return DEFAULT_REDIRECT_TO
    return value


@auth_bp.get("/line/login")
def line_login():
    url = get_container().line_login.build_authorize_url(
        redirect_to=_safe_redirect_to(request.args.get("redirect_to"))
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
    tokens = container.auth_sessions.start(
        container.users.to_principal(user),
        provider="line_login",
        line_tokens=result.tokens,
        user_agent=request.user_agent.string,
        ip_address=request.remote_addr,
    )
    return render_template(
        "auth/callback.html",
        display_name=identity.display_name,
        tokens_json=json.dumps(tokens.to_dict()),
        redirect_to=result.redirect_to,
    )


@auth_bp.post("/refresh")
@rate_limit("auth_refresh", limit=30, per_seconds=300)
def refresh():
    body = request.get_json(silent=True) or {}
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise ValidationError("缺少 refresh_token")
    container = get_container()
    tokens = container.auth_sessions.refresh(
        refresh_token,
        load_principal=lambda user_id: container.users.to_principal(container.users.get(user_id)),
    )
    return ok(tokens.to_dict())


@auth_bp.post("/logout")
def logout():
    body = request.get_json(silent=True) or {}
    get_container().auth_sessions.logout(body.get("refresh_token"))
    return ok({"logged_out": True})


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
