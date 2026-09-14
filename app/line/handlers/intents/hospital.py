"""找醫院 → 院所查詢 + 即時壅塞燈號（不需基本資料）。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class FindHospitalHandler(IntentHandler):
    intent = Intent.FIND_HOSPITAL
    feature_name = "找醫院"
    required_permission = Permission.CONSULT_HOSPITAL

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        # TODO: 接上院所查詢服務（位置分享 → 附近院所 + 壅塞燈號；emergency=1 時只列急救責任醫院）
        if params.get("emergency") == "1":
            ctx.reply(M.text("正在為你查詢最近的急救責任醫院……（功能開發中）"))
            return
        ctx.reply(M.text("請分享你的位置，或輸入地區 / 院所名稱，我會列出附近院所與即時壅塞燈號。"))
