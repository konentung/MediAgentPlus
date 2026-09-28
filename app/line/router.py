"""IntentRouter：Intent → IntentHandler 的註冊與分派。"""

from __future__ import annotations

from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.assistant import AssistantHandler
from app.line.handlers.intents.base import IntentHandler
from app.line.handlers.intents.discharge import DischargeHandler
from app.line.handlers.intents.feedback import FeedbackHandler
from app.line.handlers.intents.hospital import FindHospitalHandler
from app.line.handlers.intents.hospital_confirm import HospitalConfirmHandler
from app.line.handlers.intents.medication import MedicationHandler
from app.line.handlers.intents.menu import MenuHandler
from app.line.handlers.intents.reminder import ReminderHandler
from app.line.handlers.intents.settings import SettingsHandler
from app.line.handlers.intents.symptom import EmergencyHandler, SymptomHandler
from app.line.intents import IntentRequest
from app.line.messages import common


class IntentRouter:
    def __init__(self) -> None:
        self._handlers: dict[Intent, IntentHandler] = {}

    def register(self, handler: IntentHandler) -> IntentRouter:
        self._handlers[handler.intent] = handler
        return self

    def handler_for(self, intent: Intent) -> IntentHandler | None:
        return self._handlers.get(intent)

    def route(self, ctx: BotContext, request: IntentRequest | None) -> None:
        handler = self._handlers.get(request.intent) if request else None
        if handler is None:
            ctx.reply(common.fallback_messages())
            return
        handler.execute(ctx, request.params)  # type: ignore[union-attr]

    @classmethod
    def default(cls) -> IntentRouter:
        router = cls()
        for handler in (
            SymptomHandler(),
            EmergencyHandler(),
            FindHospitalHandler(),
            DischargeHandler(),
            MedicationHandler(),
            ReminderHandler(),
            FeedbackHandler(),
            SettingsHandler(),
            HospitalConfirmHandler(),
            MenuHandler(),
        ):
            router.register(handler)
        router.register(AssistantHandler(router))
        return router
