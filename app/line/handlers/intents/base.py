"""IntentHandler：Rich Menu 六格與其他意圖的處理基底，內建權限 / 綁定檢查。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.domain.enums import Intent, Permission
from app.domain.rbac import AccessPolicy
from app.line.context import BotContext
from app.line.messages import common


class IntentHandler(ABC):
    intent: ClassVar[Intent]
    feature_name: ClassVar[str] = ""
    required_permission: ClassVar[Permission | None] = None
    requires_verified: ClassVar[bool] = False  # 「基本資料已填且驗證通過？」菱形判斷

    def execute(self, ctx: BotContext, params: dict[str, str]) -> None:
        if ctx.principal.is_locked:
            ctx.reply(common.locked_messages())
            return
        if self.requires_verified and not ctx.principal.is_verified:
            ctx.reply(common.binding_required_messages(ctx.liff_url("/binding"), self.feature_name))
            return
        if self.required_permission and not AccessPolicy.has_permission(
            ctx.principal, self.required_permission
        ):
            ctx.reply(common.binding_required_messages(ctx.liff_url("/binding"), self.feature_name))
            return
        self.handle(ctx, params)

    @abstractmethod
    def handle(self, ctx: BotContext, params: dict[str, str]) -> None: ...
