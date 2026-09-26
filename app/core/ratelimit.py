"""簡易 rate limit（固定視窗、單一程序記憶體）。

用途：登入換 token、身分證重複檢查、身分證明碼讀取這類容易被暴力嘗試或批次撈取的端點。
未設定 `REDIS_URL` 時用單一程序記憶體（各 worker 各算各的）；設定後改用 Redis，
多 worker / 多機共用同一份計數。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from flask import current_app, g, request

from app.core.exceptions import AppError

F = TypeVar("F", bound=Callable)


class RateLimitError(AppError):
    status_code = 429
    error_code = "rate_limited"


class MemoryRateLimiter:
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


class RedisRateLimiter:
    """固定視窗：`INCR` 後對第一次命中的 key 設定 `EXPIRE`。"""

    def __init__(self, client: Any, prefix: str = "ratelimit:") -> None:
        self._redis = client
        self._prefix = prefix

    def hit(self, key: str, limit: int, per_seconds: int) -> bool:
        redis_key = self._prefix + key
        count = int(self._redis.incr(redis_key))
        if count == 1:
            self._redis.expire(redis_key, per_seconds)
        return count <= limit


def build_limiter(redis_client: Any | None) -> MemoryRateLimiter | RedisRateLimiter:
    return RedisRateLimiter(redis_client) if redis_client is not None else MemoryRateLimiter()


def get_limiter() -> MemoryRateLimiter | RedisRateLimiter:
    limiter = current_app.extensions.get("rate_limiter")
    if limiter is None:
        limiter = current_app.extensions["rate_limiter"] = build_limiter(None)
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
