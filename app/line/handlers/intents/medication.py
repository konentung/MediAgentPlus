"""去哪拿藥 → ③ 藥物諮詢：拍處方箋辨識、找健保特約藥局、藥名查詢（不需基本資料）。

流程（對話狀態存在 `container.conversation_state`）：
  menu → 設定「等處方箋照片」→ 使用者傳圖 → image：loading 動畫 → 下載 → 模型辨識 → 結果 Flex
  confirm → 設定「等位置」→ 使用者分享位置 → locate：依地址找同區藥局 → Carousel
"""

from __future__ import annotations

import logging

from app.core.exceptions import ExternalServiceError, ValidationError
from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import medication as msg
from app.line.messages.builder import MessageBuilder as M
from app.line.state_store import (
    AWAIT_DRUG_NAME,
    AWAIT_LOCATION,
    AWAIT_PRESCRIPTION_PHOTO,
    PENDING_LOCATION,
)

logger = logging.getLogger(__name__)

CONTROLLED_NOTE = "※ 處方若含管制藥品，部分藥局可能無法調劑，建議先電話確認或回原院所藥局。"


class MedicationHandler(IntentHandler):
    intent = Intent.MEDICATION
    feature_name = "藥物諮詢"
    required_permission = Permission.CONSULT_MEDICATION_ONCE

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        action = params.get("action", "menu")
        handler = getattr(self, f"_action_{action}", None)
        if handler is None:
            self._action_menu(ctx, params)
            return
        handler(ctx, params)

    # ---- 入口 ----

    def _action_menu(self, ctx: BotContext, params: dict[str, str]) -> None:
        self._states(ctx).set(ctx.line_user_id, AWAIT_PRESCRIPTION_PHOTO)
        ctx.reply(msg.menu_messages())

    def _action_photo(self, ctx: BotContext, params: dict[str, str]) -> None:
        self._states(ctx).set(ctx.line_user_id, AWAIT_PRESCRIPTION_PHOTO)
        ctx.reply(msg.photo_prompt_messages())

    def _action_image_ask(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(msg.image_ask_messages(params.get("message_id", "")))

    def _action_ignore_image(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(M.text("好的。需要時可從主選單「去哪拿藥」拍處方箋。"))

    def _action_location_ask(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(msg.location_ask_messages())

    # ---- 辨識 ----

    def _action_image(self, ctx: BotContext, params: dict[str, str]) -> None:
        message_id = params.get("message_id")
        if not message_id:
            ctx.reply(msg.photo_prompt_messages())
            return
        line = ctx.container.line_client
        try:
            line.show_loading_animation(ctx.line_user_id, 60)
        except Exception:  # noqa: BLE001 - 動畫失敗不影響主流程
            logger.debug("loading animation failed", exc_info=True)
        try:
            image = line.get_message_content(message_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to download LINE image %s", message_id)
            ctx.reply(msg.ocr_failed_messages("圖片下載失敗，請再傳一次。"))
            return
        try:
            prescription = ctx.container.prescriptions.create_from_image(
                ctx.principal, image, _sniff_content_type(image), message_id
            )
        except ValidationError as exc:
            if exc.details and exc.details.get("not_prescription"):
                self._states(ctx).set(ctx.line_user_id, AWAIT_PRESCRIPTION_PHOTO)
                ctx.reply(msg.not_prescription_messages())
                return
            ctx.reply(msg.ocr_failed_messages(exc.message))
            return
        except ExternalServiceError as exc:
            self._states(ctx).set(ctx.line_user_id, AWAIT_PRESCRIPTION_PHOTO)
            ctx.reply(msg.ocr_failed_messages(exc.message))
            return
        self._states(ctx).clear(ctx.line_user_id)
        messages = msg.prescription_result_messages(
            prescription, ctx.liff_url(f"/prescriptions/{prescription['id']}")
        )
        try:
            ctx.reply(messages)
        except Exception:  # noqa: BLE001 - 辨識太久 reply token 過期（約 1 分鐘）時改用 push
            logger.warning("Reply token expired after OCR; falling back to push", exc_info=True)
            ctx.push(messages)

    # ---- 找藥局 ----

    def _action_confirm(self, ctx: BotContext, params: dict[str, str]) -> None:
        prescription_id = params.get("prescription_id", "")
        try:
            ctx.container.prescriptions.confirm(ctx.principal, prescription_id)
        except ValidationError as exc:
            ctx.reply(M.text(exc.message))
            return
        self._prompt_location(ctx, prescription_id)

    def _action_locate_prompt(self, ctx: BotContext, params: dict[str, str]) -> None:
        self._prompt_location(ctx, params.get("prescription_id"))

    def _prompt_location(self, ctx: BotContext, prescription_id: str | None) -> None:
        payload = {"for": "pharmacy"}
        if prescription_id:
            payload["prescription_id"] = prescription_id
        self._states(ctx).set(ctx.line_user_id, AWAIT_LOCATION, payload)
        ctx.reply(msg.location_prompt_messages(self._profile_address(ctx) is not None))

    def _action_locate(self, ctx: BotContext, params: dict[str, str]) -> None:
        address = params.get("address")
        source = params.get("source")
        if source == "profile":
            address = self._profile_address(ctx)
        elif source == "state":
            state = self._states(ctx).pop(ctx.line_user_id)
            if state and state.action == PENDING_LOCATION:
                address = state.payload.get("address")
        if not address:
            self._prompt_location(ctx, params.get("prescription_id"))
            return
        self._states(ctx).clear(ctx.line_user_id)
        result = ctx.container.pharmacies.find_by_address(address)
        ctx.reply(
            msg.pharmacy_result_messages(result, CONTROLLED_NOTE if result["pharmacies"] else None)
        )

    # ---- 藥名查詢 ----

    def _action_drug(self, ctx: BotContext, params: dict[str, str]) -> None:
        self._states(ctx).set(ctx.line_user_id, AWAIT_DRUG_NAME)
        ctx.reply(msg.drug_prompt_messages())

    def _action_lookup(self, ctx: BotContext, params: dict[str, str]) -> None:
        query = (params.get("q") or "").strip()
        if not query:
            self._action_drug(ctx, params)
            return
        drugs = ctx.container.pharmacies.search_drugs(query)
        ctx.reply(msg.drug_lookup_messages(query, drugs))

    # ---- helpers ----

    @staticmethod
    def _states(ctx: BotContext):
        return ctx.container.conversation_state

    @staticmethod
    def _profile_address(ctx: BotContext) -> str | None:
        """已填基本資料的使用者可直接用住址找藥局。"""
        if not ctx.principal.user_id:
            return None
        profile = ctx.container.patient_repo.get_profile(ctx.principal.user_id) or {}
        city, district = profile.get("address_city"), profile.get("address_district")
        if not city:
            return None
        return f"{city}{district or ''}{profile.get('address_street') or ''}"


def _sniff_content_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
