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
        self._sync_profile(ctx)
        # 歡迎訊息 → 安全聲明 → 資料使用說明 Flex（引導至 LIFF 基本資料表單）
        ctx.reply(welcome_messages(ctx.liff_url("/intake")))

    @staticmethod
    def _sync_profile(ctx: BotContext) -> None:
        """加好友當下就向 LINE 取顯示名稱 / 頭像存進 users，後台才認得出是誰；失敗只記 log。"""
        if not ctx.line_user_id:
            return
        try:
            profile = ctx.container.line_client.get_profile(ctx.line_user_id)
            ctx.container.users.get_or_create_by_line(
                ctx.line_user_id,
                display_name=profile.display_name,
                picture_url=profile.picture_url,
            )
        except Exception:
            logger.warning("取得 LINE 個人資料失敗: %s", ctx.line_user_id, exc_info=True)


class UnfollowHandler(EventHandler):
    event_type = UnfollowEvent

    def handle(self, ctx: BotContext) -> None:
        # 使用者封鎖：關掉推播，`flask reminder run-due` 會略過 push_enabled=False 的人
        logger.info("User unfollowed: %s", ctx.line_user_id)
        if ctx.user is not None:
            ctx.container.user_repo.update(ctx.user.id, push_enabled=False)
