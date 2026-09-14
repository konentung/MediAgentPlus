"""Rich Menu 管理：主選單常駐 6 格（3 欄 × 2 列，2500×1686）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from linebot.v3.messaging import (
    PostbackAction,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
)

from app.domain.enums import Intent
from app.line.client import LineMessagingClient
from app.line.intents import postback_data

MENU_WIDTH, MENU_HEIGHT = 2500, 1686
COLUMNS, ROWS = 3, 2


@dataclass(frozen=True)
class MenuCell:
    label: str
    intent: Intent


# 順序：左上 → 右下
DEFAULT_CELLS: tuple[MenuCell, ...] = (
    MenuCell("我不舒服", Intent.SYMPTOM),
    MenuCell("找醫院", Intent.FIND_HOSPITAL),
    MenuCell("我剛出院", Intent.DISCHARGE),
    MenuCell("去哪拿藥", Intent.MEDICATION),
    MenuCell("服藥提醒", Intent.REMINDER),
    MenuCell("找真人", Intent.HUMAN),
)


class RichMenuManager:
    def __init__(self, client: LineMessagingClient) -> None:
        self._client = client

    @staticmethod
    def build_request(
        cells: tuple[MenuCell, ...] = DEFAULT_CELLS, name: str = "mediagent-main"
    ) -> RichMenuRequest:
        cell_w, cell_h = MENU_WIDTH // COLUMNS, MENU_HEIGHT // ROWS
        areas = []
        for index, cell in enumerate(cells):
            col, row = index % COLUMNS, index // COLUMNS
            areas.append(
                RichMenuArea(
                    bounds=RichMenuBounds(
                        x=col * cell_w, y=row * cell_h, width=cell_w, height=cell_h
                    ),
                    action=PostbackAction(
                        label=cell.label, data=postback_data(cell.intent), display_text=cell.label
                    ),
                )
            )
        return RichMenuRequest(
            size=RichMenuSize(width=MENU_WIDTH, height=MENU_HEIGHT),
            selected=True,
            name=name,
            chat_bar_text="主選單",
            areas=areas,
        )

    def setup_default(self, image_path: Path, replace_existing: bool = True) -> str:
        """建立主選單並設為預設；replace_existing=True 會先清掉舊的 Rich Menu。"""
        if replace_existing:
            for rich_menu_id in self._client.list_rich_menus():
                self._client.delete_rich_menu(rich_menu_id)
        content_type = (
            "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        )
        rich_menu_id = self._client.create_rich_menu(
            self.build_request(), image_path.read_bytes(), content_type
        )
        self._client.set_default_rich_menu(rich_menu_id)
        return rich_menu_id
