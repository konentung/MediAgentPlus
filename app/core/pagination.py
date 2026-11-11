"""後台表格分頁的共用格式（藥局 / 藥品 / 院所都用）。"""

from __future__ import annotations

from typing import Any

PAGE_SIZE = 15  # 後台表格每頁筆數


def page_payload(items: list[dict[str, Any]], total: int, page: int) -> dict[str, Any]:
    """API 回給前端的分頁物件；`Admin.pagination()` 依 page / pages / total / per_page 畫頁碼。"""
    pages = max(1, -(-total // PAGE_SIZE))  # ceil
    return {"items": items, "total": total, "page": page, "pages": pages, "per_page": PAGE_SIZE}
