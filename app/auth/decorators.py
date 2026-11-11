"""API 授權裝飾器：require_auth / require_permission / require_role / require_verified。

用法：
    @bp.get("/me")
    @require_auth()
    def me(): ...

    @bp.put("/admin/users/<user_id>/role")
    @require_permission(Permission.STAFF_MANAGE)
    def set_role(user_id): ...
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from flask import g, request

from app.core.container import get_container
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.domain.enums import Permission, Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy

F = TypeVar("F", bound=Callable)


def current_principal() -> Principal:
    """取得目前請求的 Principal；未登入時回傳匿名 GUEST。"""
    principal = getattr(g, "principal", None)
    if principal is None:
        principal = _resolve_principal()
        g.principal = principal
    return principal


def _resolve_principal() -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return Principal.anonymous()
    token = header.removeprefix("Bearer ").strip()
    container = get_container()
    claims = container.jwt.decode(token)
    user_id = claims.get("sub")
    # 角色 / 綁定狀態一律以資料庫為準，token 裡的值可能已經過期
    user = container.user_repo.get_by_id(user_id) if user_id else None
    if user is None:
        raise AuthenticationError("使用者不存在或已停用")
    return container.users.to_principal(user)


def require_auth(optional: bool = False) -> Callable[[F], F]:
    """要求已登入；optional=True 時允許匿名但仍解析 Principal。"""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            principal = current_principal()
            if not optional and not principal.is_authenticated:
                raise AuthenticationError("請先登入")
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_permission(*permissions: Permission, any_of: bool = False) -> Callable[[F], F]:
    """要求持有所有（或任一）指定權限。"""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            principal = current_principal()
            if not principal.is_authenticated:
                raise AuthenticationError("請先登入")
            check = AccessPolicy.has_any if any_of else AccessPolicy.has_all
            if not check(principal, permissions):
                raise AuthorizationError(
                    "權限不足", details={"required": [p.value for p in permissions]}
                )
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_role(*roles: Role) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            principal = current_principal()
            if not principal.is_authenticated:
                raise AuthenticationError("請先登入")
            if principal.role not in roles:
                raise AuthorizationError("角色不符", details={"required": [r.value for r in roles]})
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_verified() -> Callable[[F], F]:
    """要求「基本資料已填且驗證通過」（個人化衛教、服藥提醒等）。"""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            principal = current_principal()
            if not principal.is_authenticated:
                raise AuthenticationError("請先登入")
            if not principal.is_verified:
                raise AuthorizationError(
                    "需完成基本資料綁定", details={"binding_status": principal.binding_status.value}
                )
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
