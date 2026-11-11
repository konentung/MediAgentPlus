"""基本資料：「基本資料」關鍵字 / Quick Reply / AI `event=intake` → 回一張 Flex，

列出「基本資料表」與「我的就醫院所」兩張 LIFF 表單的按鈕（不直接開 LIFF）。
"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import common


class IntakeHandler(IntentHandler):
    intent = Intent.INTAKE
    feature_name = "基本資料"
    required_permission = Permission.INTAKE_WRITE_OWN
    requires_verified = False

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(
            common.intake_messages(
                ctx.liff_url("/intake"),
                ctx.liff_url("/hospitals"),
                ctx.principal.binding_status,
            )
        )
