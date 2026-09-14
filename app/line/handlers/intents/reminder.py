"""服藥提醒 → ④ 服藥小幫手跨日排程提醒（需基本資料已驗證）。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class ReminderHandler(IntentHandler):
    intent = Intent.REMINDER
    feature_name = "服藥提醒"
    required_permission = Permission.REMINDER_MANAGE_OWN
    requires_verified = True

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        # TODO: 接上排程服務（建立 / 查看 / 回報副作用 → 轉真人）
        action = params.get("action", "menu")
        if action == "create":
            ctx.reply(
                M.text(
                    "請告訴我藥名與服用時間（例如：Metformin 早晚各一次），我會幫你建立跨日提醒。"
                )
            )
            return
        ctx.reply(
            M.text(
                "服藥小幫手：你想做什麼？",
                quick_replies=[
                    M.postback_item("建立提醒", Intent.REMINDER, action="create"),
                    M.postback_item("查看提醒", Intent.REMINDER, action="list"),
                    M.postback_item("回報副作用", Intent.HUMAN, reason="side_effect"),
                ],
            )
        )
