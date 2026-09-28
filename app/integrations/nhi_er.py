"""健保署「急診即時訊息」API 客戶端。

    POST https://info.nhi.gov.tw/api/inae4000/inae4001s01/SQL0002
    {"AREA_NO": "", "CONT_TYPE": ""}   → 全國 59 家重度級急救責任醫院

完全公開、不需金鑰。回傳欄位（駝峰大小寫很怪，是官方原樣）：
    hosP_ID / hosP_NAME / areA_NO_N / conT_TYPE / inform(Y=119 滿床) /
    waiT_SEE_CNT / waiT_BED_CNT / waiT_GENERAL_CNT / waiT_ICU_CNT / txT_DATE
測試一律以 `tests/conftest.py` 的 `FakeErClient` 替換 `container.er_client`。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

import requests
from pydantic import BaseModel, Field

from app.core.exceptions import ExternalServiceError
from app.utils.clock import TAIPEI

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://info.nhi.gov.tw/api/inae4000/inae4001s01/SQL0002"
_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M")


class ErReport(BaseModel):
    """一家醫院的一筆即時回報（已正規化）。"""

    nhi_hospital_id: str
    hospital_name: str
    area_code: str
    hosp_type: str = "2"  # 1=醫學中心 2=區域 3=地區
    full_119: bool = False
    wait_see: int = 0
    wait_bed: int = 0
    wait_admit: int = 0
    wait_icu: int = 0
    reported_at: datetime  # UTC aware


class ErFeed(BaseModel):
    fetched_at: datetime
    sysdate: str = ""
    reports: list[ErReport] = Field(default_factory=list)


class ErClient(Protocol):
    def fetch(self) -> ErFeed: ...


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def parse_reported_at(text: str | None, fallback: datetime) -> datetime:
    """txT_DATE 是台灣時間（格式不固定），轉成 UTC aware；解析失敗用 fallback。"""
    for fmt in _DATE_FORMATS:
        try:
            local = datetime.strptime((text or "").strip(), fmt).replace(tzinfo=TAIPEI)
            return local.astimezone(UTC)
        except ValueError:
            continue
    return fallback


def parse_feed(payload: dict[str, Any], fetched_at: datetime | None = None) -> ErFeed:
    """官方 JSON → ErFeed。缺 hosP_ID 或地區代碼的列略過。"""
    fetched_at = fetched_at or datetime.now(UTC)
    sysdate = str(payload.get("sysdate") or "")
    default_time = parse_reported_at(sysdate, fetched_at)
    reports: list[ErReport] = []
    for raw in payload.get("data") or []:
        hospital_id = str(raw.get("hosP_ID") or "").strip()
        area = str(raw.get("areA_NO_N") or "").strip()
        if not hospital_id or not area:
            continue
        reports.append(
            ErReport(
                nhi_hospital_id=hospital_id[:16],
                hospital_name=str(raw.get("hosP_NAME") or hospital_id).strip()[:100],
                area_code=area[:4],
                hosp_type=str(raw.get("conT_TYPE") or "2").strip()[:2],
                full_119=str(raw.get("inform") or "").strip().upper() == "Y",
                wait_see=_safe_int(raw.get("waiT_SEE_CNT")),
                wait_bed=_safe_int(raw.get("waiT_BED_CNT")),
                wait_admit=_safe_int(raw.get("waiT_GENERAL_CNT")),
                wait_icu=_safe_int(raw.get("waiT_ICU_CNT")),
                reported_at=parse_reported_at(raw.get("txT_DATE"), default_time),
            )
        )
    return ErFeed(fetched_at=fetched_at, sysdate=sysdate, reports=reports)


class NhiErClient:
    def __init__(self, url: str = DEFAULT_API_URL, timeout_seconds: int = 20) -> None:
        self._url = url
        self._timeout = timeout_seconds

    def fetch(self) -> ErFeed:
        try:
            resp = requests.post(
                self._url,
                json={"AREA_NO": "", "CONT_TYPE": ""},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("NHI ER API fetch failed: %s", exc)
            raise ExternalServiceError("健保署急診即時資料暫時無法取得") from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError("健保署急診即時資料格式異常")
        return parse_feed(payload)
