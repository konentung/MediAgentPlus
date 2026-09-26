"""時間工具：所有「今天」的判斷一律用台灣時區，避免 UTC 伺服器在 00:00–08:00 差一天。"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def today_tw() -> date:
    return datetime.now(TAIPEI).date()
