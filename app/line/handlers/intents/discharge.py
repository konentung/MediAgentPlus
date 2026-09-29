"""我剛出院（衛教諮詢）→ 基本資料已驗證？

是：② 衛教個人化版（AI 依最近診斷 + 知識庫生成）；否：② 衛教通用版（選科別與處置）。
"""

from __future__ import annotations

import logging

from app.core.exceptions import ExternalServiceError
from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import assistant as ai_msg
from app.line.messages.builder import MessageBuilder as M

logger = logging.getLogger(__name__)

# Rich Menu 進來沒有使用者文字，用固定問題讓 AI 依「病人資料」與知識庫生成個人化衛教
PERSONAL_QUESTION = "我剛出院回家，現在該注意什麼、要做哪些事？"


class DischargeHandler(IntentHandler):
    intent = Intent.DISCHARGE
    feature_name = "出院衛教"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        if ctx.principal.is_verified and self._reply_personal(ctx):
            return
        # 通用版：先選科別與處置，並提供「想要個人化」入口
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
                    "uri": ctx.liff_url("/intake"),
                },
            ],
        )
        ctx.reply(M.flex("出院衛教", bubble))

    def _reply_personal(self, ctx: BotContext) -> bool:
        """個人化版：AI 依最近就診診斷 + 慢性病 + 衛教知識庫回「現在該做什麼」。

        AI 失敗或沒有院方紀錄（answer 留空）回 False，改走通用版。
        """
        try:
            decision = ctx.container.conversation.decide(ctx.principal, PERSONAL_QUESTION)
        except ExternalServiceError as exc:
            logger.warning("Personal discharge education unavailable: %s", exc.message)
            return False
        if not decision.answer:
            return False
        actions = decision.actions or ["reminder_menu", "human"]
        ctx.reply(
            ai_msg.answer_messages(
                decision.answer, actions, ctx.liff_url("/intake"), decision.sources
            )
        )
        return True
