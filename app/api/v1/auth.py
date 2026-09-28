"""LINE Login（網頁端 OAuth）與自家 JWT 端點。

GET  /api/v1/auth/line/login?redirect_to=...  → 302 導向 LINE 授權頁
GET  /api/v1/auth/line/callback?code&state     → 換發 JWT，render 登入成功頁（Jinja）
GET  /api/v1/auth/me                           → 目前身分與權限

沒有 refresh token 與伺服器端登出：access token 到期（`JWT_ACCESS_EXPIRES_SECONDS`，
院方角色 `JWT_STAFF_ACCESS_EXPIRES_SECONDS`）就重新登入，登出只是前端清掉 sessionStorage。
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from flask import Blueprint, redirect, render_template, request

from app.auth.decorators import current_principal, require_auth
from app.core.container import get_container
from app.core.responses import ok
from app.domain.enums import Role
from app.domain.rbac import ROLE_ASSIGNABLE_BY, permissions_for

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

DEFAULT_REDIRECT_TO = "/"


def _safe_redirect_to(value: str | None) -> str:
    """登入後只允許導向站內相對路徑，避免 open redirect；未指定則回首頁。

    瀏覽器會把 `/\\evil.com` 的反斜線當成斜線，所以除了 `//` 也要擋反斜線，
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
    tokens = container.jwt.issue(container.users.to_principal(user))
    return render_template(
        "auth/callback.html",
        display_name=identity.display_name,
        tokens_json=json.dumps(tokens.to_dict()),
        redirect_to=result.redirect_to,
    )


@auth_bp.get("/me")
@require_auth()
def me():
    principal = current_principal()
    return ok(
        {
            **principal.to_dict(),
            "permissions": sorted(p.value for p in permissions_for(principal.role)),
            # 依 Role 列舉順序，供後台「人員與角色」下拉選單直接使用
            "assignable_roles": [
                r.value for r in Role if r in ROLE_ASSIGNABLE_BY.get(principal.role, frozenset())
            ],
        }
    )
