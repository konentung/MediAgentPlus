"""簡易 rate limit（固定視窗、單程序記憶體）。

用途：登入換 token、身分證重複檢查這類容易被暴力嘗試的端點。多 worker 時各算各的。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from flask import current_app, g, request

from app.core.exceptions import AppError

F = TypeVar("F", bound=Callable)


class RateLimitError(AppError):
    status_code = 429
    error_code = "rate_limited"


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}  # key → (視窗起點, 次數)

    def hit(self, key: str, limit: int, per_seconds: int) -> bool:
        """記一次呼叫；超過上限回 False。"""
        now = time.monotonic()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= per_seconds:
                start, count = now, 0
            count += 1
            self._buckets[key] = (start, count)
            if len(self._buckets) > 10_000:  # 避免無限成長
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[0] < per_seconds}
        return count <= limit


def get_limiter() -> RateLimiter:
    limiter = current_app.extensions.get("rate_limiter")
    if limiter is None:
        limiter = current_app.extensions["rate_limiter"] = RateLimiter()
    return limiter


def rate_limit(name: str, *, limit: int, per_seconds: int, by: str = "ip") -> Callable[[F], F]:
    """裝飾器：`by="ip"` 以來源 IP 計數，`by="user"` 以已登入使用者計數（未登入退回 IP）。

    `RATELIMIT_ENABLED=False`（測試環境）時不計數。
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if current_app.config.get("RATELIMIT_ENABLED", True):
                principal = getattr(g, "principal", None)
                subject = (
                    principal.user_id
                    if by == "user" and principal is not None and principal.user_id
                    else request.remote_addr or "unknown"
                )
                if not get_limiter().hit(f"{name}:{subject}", limit, per_seconds):
                    raise RateLimitError("請求過於頻繁，請稍後再試")
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
