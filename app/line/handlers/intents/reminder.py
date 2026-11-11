"""服藥提醒 → ④ 服藥小幫手：查看排程、從處方建立（LIFF）、推播按鈕回報（已服用 / 稍後 / 跳過）。

不需驗證基本資料：提醒是病人自己的資料，任何加入好友的使用者都能用。
"""

from __future__ import annotations

from app.core.exceptions import AppError
from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import reminder as msg
from app.line.messages.builder import MessageBuilder as M


class ReminderHandler(IntentHandler):
    intent = Intent.REMINDER
    feature_name = "服藥小幫手"
    required_permission = Permission.REMINDER_MANAGE_OWN
    requires_verified = False

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        action = params.get("action", "menu")
        reminders = ctx.container.reminders
        liff_url = ctx.liff_url("/reminders")

        if action == "list":
            data = reminders.list_own(ctx.principal)
            ctx.reply(msg.list_messages(data["schedules"], liff_url))
            return

        if action == "create":
            prescription_id = params.get("prescription_id")
            url = f"{liff_url}?prescription={prescription_id}" if prescription_id else liff_url
            bubble = M.button_bubble(
                title="建立服藥提醒",
                body=(
                    "我已依處方頻率算好建議時間，請到提醒頁確認或調整後儲存。"
                    if prescription_id
                    else "到提醒頁輸入藥名與服用時間，或先拍處方箋讓我自動帶入。"
                ),
                buttons=[{"type": "uri", "label": "開啟提醒頁", "uri": url}],
            )
            ctx.reply(M.flex("建立服藥提醒", bubble))
            return

        if action in ("taken", "snooze", "skip"):
            try:
                result = reminders.respond(ctx.principal, params.get("delivery_id", ""), action)
            except AppError as exc:
                ctx.reply(M.text(exc.message))
                return
            ctx.reply(M.text(msg.response_text(action, result["drug_name"])))
            return

        if action in ("pause", "resume"):
            try:
                if action == "pause":
                    reminders.pause(ctx.principal, params.get("schedule_id", ""))
                    ctx.reply(M.text("已暫停這項提醒 7 天，之後會自動恢復。"))
                else:
                    reminders.resume(ctx.principal, params.get("schedule_id", ""))
                    ctx.reply(M.text("已恢復提醒。"))
            except AppError as exc:
                ctx.reply(M.text(exc.message))
            return

        ctx.reply(msg.menu_messages(liff_url))
