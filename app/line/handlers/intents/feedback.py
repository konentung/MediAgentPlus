"""意見回饋：Rich Menu / Quick Reply「意見回饋」→ 給 LIFF 表單連結（任何人都能填，不需驗證）。"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import common


class FeedbackHandler(IntentHandler):
    intent = Intent.FEEDBACK
    feature_name = "意見回饋"
    required_permission = Permission.FEEDBACK_SUBMIT
    requires_verified = False

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(common.feedback_messages(ctx.liff_url("/feedback")))
