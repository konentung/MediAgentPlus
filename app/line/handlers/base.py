"""Webhook 事件處理器基底。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from linebot.v3.webhooks import Event

from app.line.context import BotContext


class EventHandler(ABC):
    """每種 Webhook Event 對應一個 handler。"""

    event_type: ClassVar[type[Event]]

    @abstractmethod
    def handle(self, ctx: BotContext) -> None: ...
