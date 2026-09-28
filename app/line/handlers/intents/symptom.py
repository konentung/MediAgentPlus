"""① 我不舒服 → 病況諮詢（不需基本資料）。紅旗命中 → 緊急畫面。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import common
from app.line.messages.builder import MessageBuilder as M


class SymptomHandler(IntentHandler):
    intent = Intent.SYMPTOM
    feature_name = "病況諮詢"
    required_permission = Permission.CONSULT_SYMPTOM

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        # 自由文字描述症狀會走 AssistantHandler 的 symptom 事件；這裡只是入口提示
        ctx.reply(
            M.text(
                "請描述你目前的不適症狀（例如：發燒兩天、頭痛、傷口紅腫）。\n"
                "若出現胸痛、呼吸困難、意識不清等情況，請立即撥打 119。",
                quick_replies=[
                    M.postback_item("建議就醫 → 找醫院", Intent.FIND_HOSPITAL),
                    M.postback_item("附近診所", Intent.FIND_HOSPITAL, action="clinics"),
                ],
            )
        )


class EmergencyHandler(IntentHandler):
    intent = Intent.EMERGENCY
    feature_name = "緊急畫面"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(common.emergency_messages())
