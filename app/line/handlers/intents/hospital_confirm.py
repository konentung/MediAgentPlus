"""
病人回答「是否在某院所就醫」
院方在後台按「請病人確認」→ 推播兩個按鈕 → 病人按下後進到這裡
（`PatientHospitalService.confirm()`）；
病人確認後那家院所才能核對建檔（`PatientRecordService.verify()` 會檢查）。
病人也可以在 LIFF「我的就醫院所」自己加入院所，那條路不經過這裡。
"""

from __future__ import annotations

from app.core.exceptions import AppError
from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


class HospitalConfirmHandler(IntentHandler):
    intent = Intent.HOSPITAL_CONFIRM
    feature_name = "確認就醫院所"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        hospital_id = params.get("hospital_id", "")
        accepted = params.get("answer") == "yes"
        if not hospital_id:
            ctx.reply(M.text("這個按鈕已失效，請聯絡院所重新發送確認。"))
            return
        try:
            hospital = ctx.container.patient_hospitals.confirm(
                ctx.principal, hospital_id, accepted=accepted
            )
        except AppError as exc:
            ctx.reply(M.text(exc.message))
            return
        if accepted:
            ctx.reply(M.text(f"已確認你在「{hospital['name']}」就醫，院所核對完成後會再通知你。"))
        else:
            ctx.reply(
                M.text(
                    f"好的，已把「{hospital['name']}」從你的就醫院所移除；"
                    "之後要加回來可以到「我的就醫院所」自己加入。"
                )
            )
