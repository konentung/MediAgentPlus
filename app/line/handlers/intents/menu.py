"""主選單：輸入「主選單 / 選單 / menu」→ 列出功能按鈕。

Rich Menu 在 LINE Official Account Manager 上建立，六格的動作設成「文字」且文字等於下面的 label
（`TEXT_KEYWORDS` 完全相等才命中），就會直達功能、不經 AI。
「意見回饋」只放在 Rich Menu，主選單訊息與 Quick Reply 不列（`MENU_CELLS`），避免每個畫面都出現它。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Intent
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages.builder import MessageBuilder as M


@dataclass(frozen=True)
class MenuCell:
    label: str
    intent: Intent


# Rich Menu 六格順序：左上 → 右下（3 欄 × 2 列）
DEFAULT_CELLS: tuple[MenuCell, ...] = (
    MenuCell("我不舒服", Intent.SYMPTOM),
    MenuCell("找醫院", Intent.FIND_HOSPITAL),
    MenuCell("我剛出院", Intent.DISCHARGE),
    MenuCell("去哪拿藥", Intent.MEDICATION),
    MenuCell("服藥提醒", Intent.REMINDER),
    MenuCell("意見回饋", Intent.FEEDBACK),
)

# 主選單訊息只列功能，不列「意見回饋」（它留在 Rich Menu 第六格）
MENU_CELLS: tuple[MenuCell, ...] = tuple(
    cell for cell in DEFAULT_CELLS if cell.intent is not Intent.FEEDBACK
)

MENU_TEXT = (
    "請選擇服務：\n"
    + "\n".join(f"・{cell.label}" for cell in MENU_CELLS)
    + "\n\n也可以直接輸入上面的功能名稱，或用自己的話描述問題。"
)


class MenuHandler(IntentHandler):
    intent = Intent.MENU
    feature_name = "主選單"

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        ctx.reply(
            M.text(
                MENU_TEXT,
                quick_replies=[M.postback_item(cell.label, cell.intent) for cell in MENU_CELLS],
            )
        )
