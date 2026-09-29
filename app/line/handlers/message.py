"""文字 / 圖片 / 位置訊息與 Postback 事件 → IntentRouter。

圖片與位置本身沒有意圖，依 `conversation_state`（使用者上一步在做什麼）決定：
- 等處方箋照片 → 圖片交給藥物諮詢辨識；沒有狀態 → 先問「這是處方箋嗎？」
- 等位置 → 交給找藥局 / 找醫院 / 找附近診所；沒有狀態 → 先記下位置再問要找什麼
- 等藥名 → 自由文字當藥名查詢
- 文字完全等於 `TEXT_KEYWORDS` 的關鍵字（主選單、我不舒服、找醫院、我剛出院、去哪拿藥、
  服藥提醒、意見回饋、設定，及正式名稱別名）→ 直接走該功能，**不經 AI**：不依賴模型的確定進入點
- 其他自由文字 → AI 助理（`AssistantHandler`）判斷事件後套用模組
"""

from __future__ import annotations

from linebot.v3.webhooks import (
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
)

from app.domain.enums import Intent
from app.line import emergency
from app.line.context import BotContext
from app.line.handlers.base import EventHandler
from app.line.intents import IntentRequest, is_emergency_text, keyword_intent
from app.line.router import IntentRouter
from app.line.state_store import (
    AWAIT_DRUG_NAME,
    AWAIT_LOCATION,
    AWAIT_PRESCRIPTION_PHOTO,
    PENDING_LOCATION,
)


class MessageHandler(EventHandler):
    event_type = MessageEvent

    def __init__(self, router: IntentRouter) -> None:
        self._router = router

    def handle(self, ctx: BotContext) -> None:
        message = ctx.event.message  # type: ignore[attr-defined]
        if isinstance(message, TextMessageContent):
            self._router.route(ctx, self._text_request(ctx, message.text))
        elif isinstance(message, ImageMessageContent):
            self._router.route(ctx, self._image_request(ctx, message.id))
        elif isinstance(message, LocationMessageContent):
            self._router.route(ctx, self._location_request(ctx, message))
        else:
            # TODO: 其他型別（貼圖、檔案）
            self._router.route(ctx, None)

    def _text_request(self, ctx: BotContext, text: str) -> IntentRequest | None:
        """文字的分派順序（前三種都不經 AI）：
        1. 紅旗關鍵字 → 緊急畫面（安全層；冷卻期間內已送過就不再短路，交給 AI 依脈絡判斷）
        2. 正在「等藥名」→ 當藥名查詢
        3. 完全等於功能關鍵字（主選單、找醫院…）→ 直接走該功能
        4. 其餘自由文字 → AI 助理判斷事件；AI 未啟用時 AssistantHandler 再退回關鍵字包含比對"""
        states = ctx.container.conversation_state
        state = states.peek(ctx.line_user_id) if ctx.line_user_id else None
        normalized = text.strip()
        if is_emergency_text(normalized) and not emergency.recently_notified(ctx):
            if state:
                states.clear(ctx.line_user_id)
            return IntentRequest(Intent.EMERGENCY, {"text": normalized})
        if state and state.action == AWAIT_DRUG_NAME:
            states.clear(ctx.line_user_id)
            return IntentRequest(Intent.MEDICATION, {"action": "lookup", "q": normalized})
        if state and state.action != PENDING_LOCATION:
            states.clear(ctx.line_user_id)  # 使用者改打字聊別的，丟掉等照片 / 等位置的狀態
        direct = keyword_intent(normalized)
        if direct:
            return IntentRequest(direct, {"text": normalized})
        return IntentRequest(Intent.ASSISTANT, {"text": normalized})

    def _image_request(self, ctx: BotContext, message_id: str) -> IntentRequest:
        states = ctx.container.conversation_state
        state = states.pop(ctx.line_user_id) if ctx.line_user_id else None
        if state and state.action == AWAIT_PRESCRIPTION_PHOTO:
            return IntentRequest(Intent.MEDICATION, {"action": "image", "message_id": message_id})
        return IntentRequest(Intent.MEDICATION, {"action": "image_ask", "message_id": message_id})

    def _location_request(self, ctx: BotContext, message: LocationMessageContent) -> IntentRequest:
        states = ctx.container.conversation_state
        address = message.address or message.title or ""
        # LINE 位置訊息自帶經緯度：找醫院用它算距離；找藥局仍用地址的縣市 / 鄉鎮市區
        location = {"address": address, "lat": str(message.latitude), "lng": str(message.longitude)}
        state = states.pop(ctx.line_user_id) if ctx.line_user_id else None
        if state and state.action == AWAIT_LOCATION:
            params = {**location, **{k: str(v) for k, v in state.payload.items()}}
            if state.payload.get("for") in ("hospital", "clinics"):
                return IntentRequest(Intent.FIND_HOSPITAL, params)
            return IntentRequest(Intent.MEDICATION, {"action": "locate", **params})
        if ctx.line_user_id:
            states.set(ctx.line_user_id, PENDING_LOCATION, location)
        return IntentRequest(Intent.MEDICATION, {"action": "location_ask"})


class PostbackHandler(EventHandler):
    event_type = PostbackEvent

    def __init__(self, router: IntentRouter) -> None:
        self._router = router

    def handle(self, ctx: BotContext) -> None:
        data = ctx.event.postback.data  # type: ignore[attr-defined]
        self._router.route(ctx, IntentRequest.from_postback(data))
