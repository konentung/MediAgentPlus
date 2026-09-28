"""藥局與藥品：開放資料匯入、依地址（縣市 / 鄉鎮市區）找藥局、藥名查詢。

不用任何地圖服務：LINE 位置訊息本身帶有地址字串，從裡面抓出縣市與鄉鎮市區，
再比對藥局主檔的 `city` / `district` 欄位。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime
from typing import Any

from app.core.pagination import PAGE_SIZE, page_payload
from app.repositories.pharmacy_repository import DrugRepository, PharmacyRepository
from app.utils.clock import now_tw, today_tw
from app.utils.geo import nearest_district

logger = logging.getLogger(__name__)

CITY_PATTERN = re.compile(
    r"(台北市|新北市|桃園市|台中市|台南市|高雄市|基隆市|新竹市|嘉義市|"
    r"新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣|"
    r"澎湖縣|金門縣|連江縣)"
)
DISTRICT_PATTERN = re.compile(r"([一-鿿]{1,3}?[區鄉鎮市])")

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
_SLOT_ZH = {"上午": "am", "下午": "pm", "晚上": "night"}
SLOT_LABELS = {"am": "上午", "pm": "下午", "night": "晚上"}
PHARMACY_KIND_KEYWORDS = ("藥局", "藥師", "藥劑")

# ATC 第一層 → 中文類別（只用來在提醒訊息旁顯示「這是哪一類藥」，不做用藥建議）
ATC_CATEGORIES = {
    "A": "消化道及代謝",
    "B": "血液及造血器官",
    "C": "心血管",
    "D": "皮膚用藥",
    "G": "泌尿生殖及性荷爾蒙",
    "H": "全身性荷爾蒙",
    "J": "全身性抗感染",
    "L": "抗腫瘤及免疫調節",
    "M": "肌肉骨骼",
    "N": "神經系統",
    "P": "抗寄生蟲",
    "R": "呼吸系統",
    "S": "感覺器官",
    "V": "其他",
}


def _clip(text: str | None, limit: int) -> str | None:
    """開放資料偶有超長欄位（例如一格塞多組電話），截到欄位長度。"""
    text = (text or "").strip()
    return text[:limit] if text else None


def normalize_city(text: str | None) -> str | None:
    """「臺」統一成「台」，再抓出縣市名稱。"""
    if not text:
        return None
    match = CITY_PATTERN.search(text.replace("臺", "台"))
    return match.group(1) if match else None


def parse_address(address: str | None) -> tuple[str | None, str | None]:
    """從地址字串取出 (縣市, 鄉鎮市區)；LINE 位置訊息的地址常以「台灣」或郵遞區號開頭。"""
    if not address:
        return None, None
    text = address.replace("臺", "台")
    city_match = CITY_PATTERN.search(text)
    if not city_match:
        return None, None
    district_match = DISTRICT_PATTERN.match(text, city_match.end())
    return city_match.group(1), district_match.group(1) if district_match else None


def parse_opening_hours(text: str | None) -> dict[str, list[str]] | None:
    """「星期一上午看診、星期二下午休診…」→ {"mon": ["am", "pm"], ...}。"""
    if not text:
        return None
    hours: dict[str, list[str]] = {}
    for token in re.split(r"[、,，\s]+", text):
        match = re.match(r"星期([一二三四五六日])(上午|下午|晚上)(看診|休診)", token)
        if not match or match.group(3) != "看診":
            continue
        key = WEEKDAY_KEYS[_WEEKDAY_ZH[match.group(1)]]
        hours.setdefault(key, []).append(_SLOT_ZH[match.group(2)])
    return hours or None


def current_slot(now: datetime | None = None) -> tuple[str, str]:
    now = now or now_tw()
    slot = "am" if now.hour < 12 else "pm" if now.hour < 18 else "night"
    return WEEKDAY_KEYS[now.weekday()], slot


class PharmacyService:
    def __init__(self, pharmacies: PharmacyRepository, drugs: DrugRepository) -> None:
        self._pharmacies = pharmacies
        self._drugs = drugs

    # ---- 找藥局 ----

    def find_nearby(
        self,
        *,
        address: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """依位置找同鄉鎮市區的健保特約藥局；該區沒有就退到同縣市。

        先從地址字串抓縣市 / 鄉鎮市區；LINE 的地址是選填、且會依手機語系變成英文，
        抓不到時改用經緯度找最近的鄉鎮市區中心點（`nearest_district`，離線資料）。
        `resolved_by` 記錄用了哪一種，方便追查。
        """
        city, district = parse_address(address)
        resolved_by = "address" if city else None
        if city is None and lat is not None and lng is not None:
            area = nearest_district(lat, lng)
            if area:
                city, district = area
                resolved_by = "coords"
        if city is None:
            logger.warning(
                "Cannot resolve area for pharmacy search: address=%r lat=%r lng=%r",
                address,
                lat,
                lng,
            )
            return {
                "city": None,
                "district": None,
                "pharmacies": [],
                "scope": None,
                "resolved_by": None,
            }
        rows = self._pharmacies.search(city=city, district=district, limit=limit * 4)
        scope = "district" if rows and district else "city"
        if not rows:
            rows = self._pharmacies.search(city=city, district=None, limit=limit * 4)
        weekday, slot = current_slot()
        results = [self._decorate(row, weekday, slot) for row in rows]
        # 現在營業中的排前面，其餘維持名稱排序
        results.sort(key=lambda r: (not r["open_now"], r["name"]))
        return {
            "city": city,
            "district": district,
            "scope": scope,
            "resolved_by": resolved_by,
            "pharmacies": results[:limit],
        }

    def list_pharmacies(
        self,
        *,
        city: str | None = None,
        district: str | None = None,
        query: str | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """後台「藥局」頁：表格分頁（每頁 15 筆），可用縣市 / 鄉鎮市區 / 關鍵字篩選。"""
        rows, total = self._pharmacies.paginate(
            city=normalize_city(city),
            district=(district or "").replace("臺", "台").strip() or None,
            query=(query or "").strip() or None,
            page=page,
            per_page=PAGE_SIZE,
        )
        weekday, slot = current_slot()
        items = [
            {
                **self._decorate(row, weekday, slot),
                "city": row.get("city"),
                "district": row.get("district"),
                "active": row.get("active", True),
            }
            for row in rows
        ]
        return page_payload(items, total, page)

    def list_drugs(self, *, query: str | None = None, page: int = 1) -> dict[str, Any]:
        """後台「藥品」頁：表格分頁，可用代號 / 名稱 / 成分篩選。"""
        rows, total = self._drugs.paginate(
            query=(query or "").strip() or None, page=page, per_page=PAGE_SIZE
        )
        return page_payload([self._drug_public(r) for r in rows], total, page)

    @staticmethod
    def _decorate(row: dict[str, Any], weekday: str, slot: str) -> dict[str, Any]:
        hours = row.get("opening_hours") or {}
        today_slots = hours.get(weekday, []) if hours else []
        return {
            "id": row["id"],
            "name": row["name"],
            "phone": row.get("phone"),
            "address": row.get("address"),
            "nhi_contracted": row.get("nhi_contracted", True),
            "open_now": slot in today_slots if hours else None,  # None = 無營業時段資料
            "today_hours": [SLOT_LABELS[s] for s in today_slots],
        }

    # ---- 藥品 ----

    def search_drugs(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._drugs.search(query, limit=limit)
        return [self._drug_public(r) for r in rows]

    def match_drug(self, *names: str | None) -> dict[str, Any] | None:
        """辨識出的藥名 → 藥品主檔；依序嘗試英文名、原文，取最接近的一筆。"""
        for name in names:
            if not name:
                continue
            # 去掉劑量 / 劑型（500mg、膜衣錠），只用第一個英文詞或整段中文找
            key = re.split(r"\s+\d|\d", name.strip())[0].strip() or name.strip()
            rows = self._drugs.search(key, limit=1)
            if rows:
                return self._drug_public(rows[0])
        return None

    @staticmethod
    def _drug_public(row: dict[str, Any]) -> dict[str, Any]:
        atc = row.get("atc_code") or ""
        return {
            "nhi_code": row["nhi_code"],
            "name_en": row["name_en"],
            "name_zh": row.get("name_zh"),
            "ingredient": row.get("ingredient"),
            "dosage_form": row.get("dosage_form"),
            "atc_code": atc or None,
            "category": ATC_CATEGORIES.get(atc[:1]) if atc else None,
        }

    # ---- 匯入 ----

    def import_pharmacies(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """支援兩種格式：健保署「特約醫事機構」（只取藥局列）與衛福部「藥局基本資料」。

        衛福部檔只補「非健保特約」且開業中的藥局（特約藥局以健保署檔為準，避免重複）。
        """
        prepared: dict[str, dict[str, Any]] = {}
        skipped = 0
        today = today_tw()
        for raw in rows:
            row = {str(k).strip().lstrip("﻿"): (v or "").strip() for k, v in raw.items() if k}
            mapped = self._map_nhi(row, today) if "醫事機構代碼" in row else self._map_mohw(row)
            if mapped is None:
                skipped += 1
                continue
            prepared[mapped["id"]] = mapped
        created = updated = 0
        batch = list(prepared.values())
        for start in range(0, len(batch), 1000):
            c, u = self._pharmacies.upsert_many(batch[start : start + 1000])
            created += c
            updated += u
        return {"created": created, "updated": updated, "skipped": skipped}

    @staticmethod
    def _map_nhi(row: dict[str, str], today: date) -> dict[str, Any] | None:
        kind = row.get("醫事機構種類", "")
        if not any(k in kind for k in PHARMACY_KIND_KEYWORDS):
            return None
        address = row.get("地址", "").replace("臺", "台")
        city, district = parse_address(address)
        end_raw = row.get("終止合約或歇業日期", "")
        active = True
        if re.fullmatch(r"\d{8}", end_raw):
            end = datetime.strptime(end_raw, "%Y%m%d").date()
            active = end >= today
        return {
            "id": row["醫事機構代碼"][:24],
            "name": (row.get("醫事機構名稱") or row["醫事機構代碼"])[:100],
            "phone": _clip(row.get("電話"), 30),
            "address": _clip(address, 200),
            "city": city,
            "district": district,
            "nhi_contracted": True,
            "opening_hours": parse_opening_hours(row.get("固定看診時段")),
            "active": active,
        }

    @staticmethod
    def _map_mohw(row: dict[str, str]) -> dict[str, Any] | None:
        if "機構名稱" not in row or row.get("機構狀態", "開業") != "開業":
            return None
        if row.get("是否為健保特約藥局", "N").upper() == "Y":
            return None  # 健保特約以健保署檔為準
        city = normalize_city(row.get("地址縣市別"))
        district = row.get("地址鄉鎮市區") or None
        street = row.get("地址街道巷弄號", "")
        address = f"{city or ''}{district or ''}{street}".replace("臺", "台")
        digest = hashlib.sha1(f"{row['機構名稱']}|{address}".encode()).hexdigest()[:12]
        return {
            "id": f"MOHW-{digest}",
            "name": row["機構名稱"][:100],
            "phone": _clip(row.get("電話"), 30),
            "address": _clip(address, 200),
            "city": city,
            "district": district,
            "nhi_contracted": False,
            "opening_hours": None,
            "active": True,
        }

    def import_drugs(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """健保用藥品項：同一藥品代號多列（歷次支付價），只保留有效迄日最新的一列。"""
        latest: dict[str, tuple[str, dict[str, Any]]] = {}
        skipped = 0
        for raw in rows:
            row = {str(k).strip().lstrip("﻿"): (v or "").strip() for k, v in raw.items() if k}
            code = row.get("藥品代號", "")
            name_en = row.get("藥品英文名稱", "")
            if not code or not name_en:
                skipped += 1
                continue
            valid_to = row.get("有效迄日", "")
            values = {
                "nhi_code": code[:12],
                "name_en": name_en[:150],
                "name_zh": (row.get("藥品中文名稱") or None) and row["藥品中文名稱"][:150],
                "ingredient": (row.get("成分") or None) and row["成分"][:300],
                "dosage_form": (row.get("劑型") or None) and row["劑型"][:50],
                "atc_code": (row.get("ATC代碼") or None) and row["ATC代碼"][:10],
            }
            current = latest.get(code)
            if current is None or valid_to >= current[0]:
                latest[code] = (valid_to, values)
        created = updated = 0
        batch = [v for _, v in latest.values()]
        for start in range(0, len(batch), 2000):
            c, u = self._drugs.upsert_many(batch[start : start + 2000])
            created += c
            updated += u
        return {"created": created, "updated": updated, "skipped": skipped}

    def stats(self) -> dict[str, int]:
        return {"pharmacies": self._pharmacies.count(), "drugs": self._drugs.count()}
