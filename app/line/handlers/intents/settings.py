"""設定頁 → 開啟 LIFF 設定頁（檢視 / 修改基本資料）。"""

from __future__ import annotations

from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class SettingsHandler(IntentHandler):
    intent = Intent.SETTINGS
    feature_name = "設定"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        status = "已綁定" if ctx.principal.is_verified else "未綁定"
        bubble = M.button_bubble(
            title=f"設定（目前狀態：{status}）",
            body="你可以在此檢視基本資料，或加入 / 移除你就醫的醫院與診所。",
            buttons=[
                {"type": "uri", "label": "開啟設定頁", "uri": ctx.liff_url("/settings")},
                {"type": "uri", "label": "我的就醫院所", "uri": ctx.liff_url("/hospitals")},
            ],
        )
        ctx.reply(M.flex("設定", bubble))
