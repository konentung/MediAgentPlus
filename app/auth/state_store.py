"""OAuth state / nonce 暫存（防 CSRF 與重放）。正式環境建議換成 Redis 實作。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Protocol


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
