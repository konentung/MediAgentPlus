"""BotContext：每個 webhook 事件處理時的上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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

    @property
    def line_user_id(self) -> str | None:
        source = getattr(self.event, "source", None)
        return getattr(source, "user_id", None)

    @property
    def reply_token(self) -> str | None:
        return getattr(self.event, "reply_token", None)

    def reply(self, messages: Sequence[Message] | Message) -> None:
        if self.reply_token is None:
            return
        batch = [messages] if isinstance(messages, Message) else list(messages)
        self.container.line_client.reply(self.reply_token, batch[:5])  # LINE 一次最多 5 則

    def push(self, messages: Sequence[Message] | Message) -> None:
        if self.line_user_id is None:
            return
        batch = [messages] if isinstance(messages, Message) else list(messages)
        self.container.line_client.push(self.line_user_id, batch[:5])

    def liff_url(self, path: str = "") -> str:
        return f"https://liff.line.me/{self.liff_id}{path}"
