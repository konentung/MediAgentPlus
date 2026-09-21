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
        if params.get("action") == "skip":
            ctx.reply(
                M.text("好的，隨時可以從主選單使用服務；需要個人化功能時再填寫基本資料即可。")
            )
            return
        status = "已綁定" if ctx.principal.is_verified else "未綁定"
        bubble = M.button_bubble(
            title=f"設定（目前狀態：{status}）",
            body="你可以在此檢視 / 修改基本資料，或解除綁定。",
            buttons=[{"type": "uri", "label": "開啟設定頁", "uri": ctx.liff_url("/settings")}],
        )
        ctx.reply(M.flex("設定", bubble))
