"""自由文字 → AI 助理（RAG + 事件判斷）→ 套用既有 LINE 模組回應。

`ConversationService.decide()` 回 `AssistantDecision`；這裡只做「事件 → 模組」的對應：
  emergency  → 緊急畫面（撥 119 + 最近急救責任醫院）
  hospital   → FindHospitalHandler（請分享位置，依距離推薦）
  medication → MedicationHandler（有藥名就查藥；否則選單）
  pharmacy   → 找附近藥局；reminder / human / intake → 對應模組
  discharge  → 已驗證且模型依「病人資料」寫出個人化衛教 → 直接回答；否則 DischargeHandler 通用版
  symptom    → 回答（分級 + 建議科別）+ 依 risk 補按鈕：watch → 附近診所、urgent → 119 / 找醫院
  faq / smalltalk / unknown → 回答文字 + 下一步按鈕
模組回覆用 `ctx.capture()` 收集，跟 AI 的回答文字合併成一次 reply（LINE 上限 5 則）。
AI 失敗（未啟用、逾時、格式錯誤）退回關鍵字比對；仍無法對應才回失敗訊息。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.exceptions import ExternalServiceError
from app.domain.assistant_schema import AssistantDecision
from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.intents import TEXT_KEYWORDS
from app.line.messages import assistant as msg
from app.line.messages import common
from app.line.messages.builder import MessageBuilder as M

if TYPE_CHECKING:
    from app.line.router import IntentRouter

logger = logging.getLogger(__name__)

LOADING_SECONDS = 60  # LINE 上限；模型回覆後動畫自動消失


class AssistantHandler(IntentHandler):
    intent = Intent.ASSISTANT
    feature_name = "AI 助理"

    def __init__(self, router: IntentRouter) -> None:
        self._router = router

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        text = (params.get("text") or "").strip()
        if not text:
            ctx.reply(common.fallback_messages())
            return
        self._loading(ctx)
        try:
            decision = ctx.container.conversation.decide(ctx.principal, text)
        except ExternalServiceError as exc:
            logger.warning("Assistant unavailable (%s); falling back to keywords", exc.message)
            self._fallback(ctx, text)
            return
        messages = self._compose(ctx, text, decision)
        try:
            ctx.reply(messages)
        except Exception:  # noqa: BLE001 - 模型太慢 reply token 過期時改用 push
            logger.warning("Reply token expired after AI decision; falling back to push")
            ctx.push(messages)

    # ---- 事件 → 模組 ----

    def _compose(self, ctx: BotContext, text: str, decision: AssistantDecision) -> list:
        liff_intake = ctx.liff_url("/intake")
        event = decision.event
        if decision.risk == "emergency" or event == "emergency":
            lead = [M.text(decision.answer)] if decision.answer else []
            return lead + common.emergency_messages()

        urgent = decision.risk == "urgent"
        slots = decision.slots
        module: list = []
        if event == "hospital":
            # 找醫院只認使用者位置：不論有沒有提到縣市，都請對方分享位置
            module = self._run(ctx, Intent.FIND_HOSPITAL, {"emergency": "1"} if urgent else {})
        elif event == "medication":
            if slots.drug_name:
                module = self._run(
                    ctx, Intent.MEDICATION, {"action": "lookup", "q": slots.drug_name}
                )
            else:
                module = self._run(ctx, Intent.MEDICATION, {"action": "menu"})
        elif event == "pharmacy":
            module = self._run(ctx, Intent.MEDICATION, {"action": "locate_prompt"})
        elif event == "reminder":
            action = (
                "list" if any(k in text for k in ("查看", "有哪些", "列出", "幾點")) else "menu"
            )
            module = self._run(ctx, Intent.REMINDER, {"action": action})
        elif event == "discharge":
            if decision.answer and ctx.principal.is_verified:
                # 衛教諮詢：模型已依病人的診斷 / 慢性病與知識庫寫出「現在該做什麼」
                actions = decision.actions or ["discharge_menu", "feedback"]
                return msg.answer_messages(decision.answer, actions, liff_intake, decision.sources)
            module = self._run(ctx, Intent.DISCHARGE, {})
        elif event == "feedback":
            module = self._run(ctx, Intent.FEEDBACK, {})
        elif event == "intake":
            bubble = M.button_bubble(
                title="基本資料",
                body=decision.answer or "填寫基本資料並經院所核對後，即可使用個人化衛教與提醒。",
                buttons=[{"type": "uri", "label": "開啟基本資料表", "uri": liff_intake}],
            )
            return [M.flex("基本資料", bubble)]

        if module:
            lead = [M.text(decision.answer)] if decision.answer else []
            return (lead + module)[:5]

        # symptom / faq / smalltalk / unknown：回答 + 下一步
        actions = list(decision.actions)
        if event == "symptom" and urgent:
            for extra in ("call_119", "find_hospital"):
                if extra not in actions:
                    actions.insert(0, extra)
        elif event == "symptom" and decision.risk == "watch" and "find_clinic" not in actions:
            actions.insert(0, "find_clinic")  # 病況諮詢：建議就醫但非急症 → 就近診所最快
        if event == "unknown" and not decision.answer:
            actions = actions or ["human"]
            return [
                M.text(
                    msg.CLARIFY_TEXT if decision.needs_clarification else msg.AI_FAILED_TEXT,
                    quick_replies=msg.action_items(actions, liff_intake) or None,
                )
            ]
        return msg.answer_messages(decision.answer, actions, liff_intake, decision.sources)

    def _run(self, ctx: BotContext, intent: Intent, params: dict[str, str]) -> list:
        """執行既有模組並收集它的回覆（不直接送出）。"""
        handler = self._router.handler_for(intent)
        if handler is None:
            return []
        with ctx.capture() as captured:
            handler.execute(ctx, params)
        return captured

    def _fallback(self, ctx: BotContext, text: str) -> None:
        intent = next((v for k, v in TEXT_KEYWORDS.items() if k in text), None)
        handler = self._router.handler_for(intent) if intent else None
        if handler is None:
            ctx.reply(msg.failed_messages())
            return
        handler.execute(ctx, {"text": text})

    @staticmethod
    def _loading(ctx: BotContext) -> None:
        if not ctx.line_user_id:
            return
        try:
            ctx.container.line_client.show_loading_animation(ctx.line_user_id, LOADING_SECONDS)
        except Exception:  # noqa: BLE001 - 動畫失敗不影響主流程
            logger.debug("loading animation failed", exc_info=True)
