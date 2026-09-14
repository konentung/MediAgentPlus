"""找真人 → 轉真人客服（任何時候一鍵可按）。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import common


class HumanHandoffHandler(IntentHandler):
    intent = Intent.HUMAN
    feature_name = "真人客服"
    required_permission = Permission.HANDOFF_REQUEST

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.container.handoff.request(ctx.principal, reason=params.get("reason", "user_request"))
        ctx.reply(common.handoff_messages())
