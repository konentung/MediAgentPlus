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

SEEN_EVENT = "seen_event"  # 新增：已處理過的 webhook 事件（去重用）
EVENT_SEEN_TTL = 600


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
            if not getattr(getattr(event, "source", None), "user_id", None):
                # 修改：群組 / 聊天室成員未授權提供 userId 時沒有身分也沒有對話狀態可用，
                # 直接略過，避免 handler 以 None 當 key 讀寫狀態
                logger.info("Skip %s without userId", type(event).__name__)
                continue
            if self._is_duplicate_redelivery(event):
                continue
            try:
                handler.handle(self._build_context(event))
            except Exception:  # noqa: BLE001 - 單一事件失敗不影響同批其他事件
                logger.exception("Failed to handle %s", type(event).__name__)

    def _is_duplicate_redelivery(self, event: Event) -> bool:
        """新增：LINE 重送（redelivery）的事件若已處理過（或還在處理中）就略過。

        同一事件重送時 `webhookEventId` 相同；每個事件開始處理前先在對話狀態 store 記下
        `evt:<id>`（TTL 10 分鐘），只有標記 `isRedelivery` 的事件才比對，第一次送達一律處理。
        """
        event_id = getattr(event, "webhook_event_id", None)
        if not event_id:
            return False
        key = f"evt:{event_id}"
        store = self._container.conversation_state
        redelivery = bool(getattr(getattr(event, "delivery_context", None), "is_redelivery", False))
        if redelivery and store.peek(key) is not None:
            logger.info("Skip redelivered %s %s", type(event).__name__, event_id)
            return True
        store.set(key, SEEN_EVENT, {}, ttl=EVENT_SEEN_TTL)
        return False

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
