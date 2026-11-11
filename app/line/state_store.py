"""
對話狀態 store：記錄使用者下一則訊息的用途（等處方箋照片 / 位置 / 藥名），TTL 預設 10 分鐘
圖片與位置訊息沒有 intent，由 MessageHandler 依此狀態分派。單程序 in-memory，重啟即清空
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

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


class ConversationStateStore:
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
