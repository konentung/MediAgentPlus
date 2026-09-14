"""去哪拿藥 → ③ 藥物諮詢單次查詢（不需基本資料）；查無品項 → 轉真人。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class MedicationHandler(IntentHandler):
    intent = Intent.MEDICATION
    feature_name = "藥物諮詢"
    required_permission = Permission.CONSULT_MEDICATION_ONCE

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        # TODO: 接上藥物資料庫查詢；查無品項時提供「找真人」
        ctx.reply(
            M.text(
                "請輸入藥品名稱（中文或英文），我會提供用法、注意事項與領藥地點。",
                quick_replies=[
                    M.postback_item("一鍵建立提醒", Intent.REMINDER, action="create"),
                    M.postback_item(
                        "查無品項 → 找真人", Intent.HUMAN, reason="medication_not_found"
                    ),
                ],
            )
        )
