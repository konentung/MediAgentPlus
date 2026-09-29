"""OAuth state / nonce 暫存（防 CSRF 與重放）。

`InMemoryOAuthStateStore` 只適合單一程序；設定 `REDIS_URL` 後容器會改用
`RedisOAuthStateStore`，多 worker / 多機共用同一份 state。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class OAuthState:
    state: str
    nonce: str
    redirect_to: str | None
    expires_at: float


class OAuthStateStore(Protocol):
    def issue(self, ttl_seconds: int, redirect_to: str | None = None) -> OAuthState: ...
    def consume(self, state: str) -> OAuthState | None: ...


class InMemoryOAuthStateStore:
    def __init__(self) -> None:
        self._states: dict[str, OAuthState] = {}

    def issue(self, ttl_seconds: int, redirect_to: str | None = None) -> OAuthState:
        self._purge()
        record = OAuthState(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            redirect_to=redirect_to,
            expires_at=time.time() + ttl_seconds,
        )
        self._states[record.state] = record
        return record

    def consume(self, state: str) -> OAuthState | None:
        """state 只能使用一次。"""
        record = self._states.pop(state, None)
        if record is None or record.expires_at < time.time():
            return None
        return record

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, v in self._states.items() if v.expires_at < now]:
            self._states.pop(key, None)


class RedisOAuthStateStore:
    """以 Redis `SET ... EX` 存 state，`GETDEL` 保證只能使用一次。"""

    def __init__(self, client: Any, prefix: str = "oauth_state:") -> None:
        self._redis = client
        self._prefix = prefix

    def issue(self, ttl_seconds: int, redirect_to: str | None = None) -> OAuthState:
        record = OAuthState(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            redirect_to=redirect_to,
            expires_at=time.time() + ttl_seconds,
        )
        self._redis.set(self._prefix + record.state, json.dumps(asdict(record)), ex=ttl_seconds)
        return record

    def consume(self, state: str) -> OAuthState | None:
        # 修改：GETDEL 是 Redis 6.2+ 才有的指令，改用交易式 GET + DEL 相容舊版
        pipe = self._redis.pipeline()
        pipe.get(self._prefix + state)
        pipe.delete(self._prefix + state)
        raw, _ = pipe.execute()
        if not raw:
            return None
        data = json.loads(raw)
        record = OAuthState(**data)
        return record if record.expires_at >= time.time() else None
