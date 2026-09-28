"""對話狀態：記住使用者「下一則訊息」是要做什麼（等處方箋照片 / 等位置 / 等藥名）。

LINE 的圖片與位置訊息本身沒有意圖，靠這個短暫狀態（預設 10 分鐘）判斷要交給哪個功能。
介面比照 `OAuthStateStore`：沒有 REDIS_URL 用單程序記憶體版，有則用 Redis。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_TTL_SECONDS = 600

# 狀態名稱
AWAIT_PRESCRIPTION_PHOTO = "await_prescription_photo"
AWAIT_LOCATION = "await_location"  # payload: {"for": "pharmacy" | "hospital", "prescription_id"}
AWAIT_DRUG_NAME = "await_drug_name"
PENDING_LOCATION = "pending_location"  # 使用者先傳位置、還沒說要找什麼；payload 帶 address


@dataclass(frozen=True)
class ConversationState:
    action: str
    payload: dict[str, Any]


class ConversationStateStore(Protocol):
    def set(
        self, line_user_id: str, action: str, payload: dict[str, Any] | None = None, ttl: int = ...
    ) -> None: ...
    def peek(self, line_user_id: str) -> ConversationState | None: ...
    def pop(self, line_user_id: str) -> ConversationState | None: ...
    def clear(self, line_user_id: str) -> None: ...


class InMemoryConversationStateStore:
    def __init__(self) -> None:
        self._states: dict[str, tuple[ConversationState, float]] = {}

    def set(
        self,
        line_user_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._states[line_user_id] = (
            ConversationState(action, dict(payload or {})),
            time.time() + ttl,
        )

    def peek(self, line_user_id: str) -> ConversationState | None:
        entry = self._states.get(line_user_id)
        if entry is None:
            return None
        state, expires_at = entry
        if expires_at < time.time():
            self._states.pop(line_user_id, None)
            return None
        return state

    def pop(self, line_user_id: str) -> ConversationState | None:
        state = self.peek(line_user_id)
        self._states.pop(line_user_id, None)
        return state

    def clear(self, line_user_id: str) -> None:
        self._states.pop(line_user_id, None)


class RedisConversationStateStore:
    def __init__(self, client: Any, prefix: str = "conv_state:") -> None:
        self._client = client
        self._prefix = prefix

    def set(
        self,
        line_user_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._client.set(
            self._prefix + line_user_id,
            json.dumps({"action": action, "payload": payload or {}}, ensure_ascii=False),
            ex=ttl,
        )

    def peek(self, line_user_id: str) -> ConversationState | None:
        raw = self._client.get(self._prefix + line_user_id)
        return self._decode(raw)

    def pop(self, line_user_id: str) -> ConversationState | None:
        raw = self._client.getdel(self._prefix + line_user_id)
        return self._decode(raw)

    def clear(self, line_user_id: str) -> None:
        self._client.delete(self._prefix + line_user_id)

    @staticmethod
    def _decode(raw: Any) -> ConversationState | None:
        if not raw:
            return None
        data = json.loads(raw)
        return ConversationState(data["action"], data.get("payload") or {})
