"""加入好友 / 封鎖事件。"""

from __future__ import annotations

import logging

from linebot.v3.webhooks import FollowEvent, UnfollowEvent

from app.line.context import BotContext
from app.line.handlers.base import EventHandler
from app.line.messages.onboarding import welcome_messages

logger = logging.getLogger(__name__)


class FollowHandler(EventHandler):
    event_type = FollowEvent

    def handle(self, ctx: BotContext) -> None:
        # 歡迎訊息 → 安全聲明 → 資料使用說明 Flex（引導至 LIFF 基本資料表單 B1）
        ctx.reply(welcome_messages(ctx.liff_url("/binding")))


class UnfollowHandler(EventHandler):
    event_type = UnfollowEvent

    def handle(self, ctx: BotContext) -> None:
        # TODO: 標記使用者已封鎖，停止推播（服藥提醒 / 每日任務）
        logger.info("User unfollowed: %s", ctx.line_user_id)
