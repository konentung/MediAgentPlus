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
# 通用版「選擇科別」的選項；按下後以文字送出，交給 AI 依科別檢索衛教知識庫回答。
# 修改：名稱對齊知識庫科別（`KNOWLEDGE_DEPARTMENTS`），檢索時才能用同科別加權
DEPARTMENTS = (
    "心臟內科",
    "胸腔內科",
    "肝膽腸胃內科",
    "神經內科",
    "腎臟內科",
    "內分泌新陳代謝科",
    "一般外科",
    "骨科",
    "泌尿科",
    "婦科",
    "兒科",
    "其他科別",
)
DEPARTMENT_QUESTION = "我剛從{department}出院，回家要注意什麼、怎麼照護？"


class DischargeHandler(IntentHandler):
    intent = Intent.DISCHARGE
    feature_name = "出院衛教"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        if params.get("step") == "department":  # 新增：原本這個 postback 沒人處理，會一直回同一張卡
            self._reply_departments(ctx)
            return
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

    @staticmethod
    def _reply_departments(ctx: BotContext) -> None:
        """通用版：列科別 Quick Reply；按下後送出文字，由 AI 依科別檢索衛教知識庫回答。"""  # 新增
        items = [
            M.message_item(name, DEPARTMENT_QUESTION.format(department=name))
            for name in DEPARTMENTS
        ]
        ctx.reply(M.text("請選擇出院的科別，我會整理對應的居家照護重點：", quick_replies=items))

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
        actions = decision.actions or ["reminder_menu"]  # 修改：human 動作已隨真人客服移除
        ctx.reply(
            ai_msg.answer_messages(
                decision.answer, actions, ctx.liff_url("/intake"), decision.sources
            )
        )
        return True
