"""我剛出院 → 基本資料已驗證？是：② 衛教個人化版；否：② 衛教通用版（選科別與處置）。"""

from __future__ import annotations

from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class DischargeHandler(IntentHandler):
    intent = Intent.DISCHARGE
    feature_name = "出院衛教"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        if ctx.principal.is_verified:
            # TODO: 個人化衛教 + 每日任務推播
            ctx.reply(M.text("這是你的個人化出院衛教，我會每天推播照護任務提醒。（功能開發中）"))
            return
        # 通用版：先選科別與處置，並提供「想要個人化」入口（B1）
        bubble = M.button_bubble(
            title="出院衛教（通用版）",
            body="請選擇科別與處置，我會提供對應的居家照護重點。\n想要個人化衛教與每日任務？填寫基本資料即可解鎖。",
            buttons=[
                {
                    "type": "postback",
                    "label": "選擇科別",
                    "data": "intent=discharge&step=department",
                    "displayText": "選擇科別",
                },
                {
                    "type": "uri",
                    "label": "想要個人化 → 填寫基本資料",
                    "uri": ctx.liff_url("/binding"),
                },
            ],
        )
        ctx.reply(M.flex("出院衛教", bubble))
