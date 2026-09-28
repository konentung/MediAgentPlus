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
        # 重新加回好友：恢復推播（服藥提醒等）
        if ctx.user is not None and not ctx.user.push_enabled:
            ctx.container.user_repo.update(ctx.user.id, push_enabled=True)
        # 歡迎訊息 → 安全聲明 → 資料使用說明 Flex（引導至 LIFF 基本資料表單）
        ctx.reply(welcome_messages(ctx.liff_url("/intake")))


class UnfollowHandler(EventHandler):
    event_type = UnfollowEvent

    def handle(self, ctx: BotContext) -> None:
        # 使用者封鎖：關掉推播，`flask reminder run-due` 會略過 push_enabled=False 的人
        logger.info("User unfollowed: %s", ctx.line_user_id)
        if ctx.user is not None:
            ctx.container.user_repo.update(ctx.user.id, push_enabled=False)
