"""EventDispatcher：解析 webhook 事件 → 建立 BotContext → 分派到對應 EventHandler。"""

from __future__ import annotations

import logging

from linebot.v3.webhooks import Event

from app.core.container import ServiceContainer
from app.domain.principal import Principal
from app.line.context import BotContext
from app.line.handlers.base import EventHandler
from app.line.handlers.follow import FollowHandler, UnfollowHandler
from app.line.handlers.message import MessageHandler, PostbackHandler
from app.line.router import IntentRouter

logger = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self, container: ServiceContainer, liff_id: str) -> None:
        self._container = container
        self._liff_id = liff_id
        self._handlers: dict[type[Event], EventHandler] = {}

    def register(self, handler: EventHandler) -> EventDispatcher:
        self._handlers[handler.event_type] = handler
        return self

    def dispatch(self, events: list[Event]) -> None:
        for event in events:
            handler = self._handlers.get(type(event))
            if handler is None:
                logger.debug("No handler for event type %s", type(event).__name__)
                continue
            try:
                handler.handle(self._build_context(event))
            except Exception:  # noqa: BLE001 - 單一事件失敗不影響同批其他事件
                logger.exception("Failed to handle %s", type(event).__name__)

    def _build_context(self, event: Event) -> BotContext:
        line_user_id = getattr(getattr(event, "source", None), "user_id", None)
        user = None
        principal = Principal.anonymous()
        if line_user_id:
            # 首次接觸即建立 GUEST；顯示名稱可之後由 get_profile 補齊
            user = self._container.users.get_or_create_by_line(line_user_id)
            principal = self._container.users.to_principal(user)
        return BotContext(
            event=event,
            principal=principal,
            user=user,
            container=self._container,
            liff_id=self._liff_id,
        )

    @classmethod
    def default(cls, container: ServiceContainer, liff_id: str) -> EventDispatcher:
        router = IntentRouter.default()
        dispatcher = cls(container, liff_id)
        for handler in (
            FollowHandler(),
            UnfollowHandler(),
            MessageHandler(router),
            PostbackHandler(router),
        ):
            dispatcher.register(handler)
        return dispatcher
