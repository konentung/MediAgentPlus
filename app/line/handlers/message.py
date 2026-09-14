"""文字訊息與 Postback 事件 → IntentRouter。"""

from __future__ import annotations

from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from app.line.context import BotContext
from app.line.handlers.base import EventHandler
from app.line.intents import IntentRequest
from app.line.router import IntentRouter


class MessageHandler(EventHandler):
    event_type = MessageEvent

    def __init__(self, router: IntentRouter) -> None:
        self._router = router

    def handle(self, ctx: BotContext) -> None:
        message = ctx.event.message  # type: ignore[attr-defined]
        if not isinstance(message, TextMessageContent):
            # TODO: 位置訊息（找醫院）、圖片（藥袋辨識）等
            self._router.route(ctx, None)
            return
        # TODO: 未命中關鍵字時交由 AI 對話引擎判斷意圖（病況 / 藥物 / 衛教）
        self._router.route(ctx, IntentRequest.from_text(message.text))


class PostbackHandler(EventHandler):
    event_type = PostbackEvent

    def __init__(self, router: IntentRouter) -> None:
        self._router = router

    def handle(self, ctx: BotContext) -> None:
        data = ctx.event.postback.data  # type: ignore[attr-defined]
        self._router.route(ctx, IntentRequest.from_postback(data))
