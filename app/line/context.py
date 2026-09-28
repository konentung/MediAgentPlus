"""BotContext：每個 webhook 事件處理時的上下文。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from linebot.v3.messaging import Message
from linebot.v3.webhooks import Event

from app.core.container import ServiceContainer
from app.domain.principal import Principal
from app.repositories.user_repository import UserRecord


@dataclass
class BotContext:
    event: Event
    principal: Principal
    user: UserRecord | None
    container: ServiceContainer
    liff_id: str
    _capture: list[Message] | None = field(default=None, repr=False)

    @property
    def line_user_id(self) -> str | None:
        source = getattr(self.event, "source", None)
        return getattr(source, "user_id", None)

    @property
    def reply_token(self) -> str | None:
        return getattr(self.event, "reply_token", None)

    def reply(self, messages: Sequence[Message] | Message) -> None:
        batch = [messages] if isinstance(messages, Message) else list(messages)
        if self._capture is not None:  # AI 助理套用模組時先收集，最後合併成一次 reply
            self._capture.extend(batch)
            return
        if self.reply_token is None:
            return
        self.container.line_client.reply(self.reply_token, batch[:5])  # LINE 一次最多 5 則

    @contextmanager
    def capture(self) -> Iterator[list[Message]]:
        """在區塊內 `reply()` 不會真的送出，而是收集到回傳的 list。"""
        previous = self._capture
        buffer: list[Message] = []
        self._capture = buffer
        try:
            yield buffer
        finally:
            self._capture = previous

    def push(self, messages: Sequence[Message] | Message) -> None:
        if self.line_user_id is None:
            return
        batch = [messages] if isinstance(messages, Message) else list(messages)
        self.container.line_client.push(self.line_user_id, batch[:5])

    def liff_url(self, path: str = "") -> str:
        return f"https://liff.line.me/{self.liff_id}{path}"
