"""急診分流（找醫院）：依使用者位置推薦適合去的急救責任醫院。

資料來源是健保署「急診即時訊息」API（全國 59 家重度級急救責任醫院，每 15 分鐘更新）：
  - `flask triage poll`（cron 每 15 分鐘）：抓 API → 寫 `er_snapshots`（同一回報時間不重複）
    → 清掉過期快照
  - 查詢時快照過舊（超過 TRIAGE_STALE_MINUTES）會即時補抓一次，失敗就用既有快照

使用者端只有一件事：LINE 分享位置（或 LIFF 用瀏覽器定位）→ `board_for_location(lat, lng)`
依距離列出最近的醫院、標上壅塞燈號，推薦最近且未壅塞的一家。沒有接收者、沒有分流建議推播。
院所座標來自 `hospitals.lat / lng`（`flask hospital import data/er_hospitals.csv`）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.exceptions import ExternalServiceError, ValidationError
from app.domain import triage_rules as rules
from app.integrations.nhi_er import ErFeed
from app.repositories.hospital_repository import HospitalRepository
from app.repositories.reminder_repository import as_utc
from app.repositories.triage_repository import SnapshotRepository
from app.services.pharmacy_service import parse_address
from app.utils.clock import TAIPEI
from app.utils.geo import haversine_km, nearest_district

logger = logging.getLogger(__name__)

CLINIC_TIERS = ("clinic", "district", "regional")
NEARBY_LIMIT = 8  # 依距離找醫院時列出幾家
CLINIC_RADIUS_KM = 10  # 找附近診所的距離上限
REFRESH_COOLDOWN = timedelta(seconds=60)


class TriageService:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        hospitals: HospitalRepository,
        er_client: Callable[[], Any],
        *,
        liff_id: str,
        stale_minutes: int = 30,
        retention_days: int = 30,
    ) -> None:
        self._snapshots = snapshots
        self._hospitals = hospitals
        self._er = er_client
        self._liff_id = liff_id
        self._stale = timedelta(minutes=stale_minutes)
        self._retention = timedelta(days=retention_days)
        # 即時補抓的冷卻：API 故障時不讓每個請求都等到逾時
        self._last_refresh_attempt: datetime | None = None

    def liff_url(self, path: str = "/er") -> str:
        return f"https://liff.line.me/{self._liff_id}{path}"

    # ---- 排程：抓取 ----

    def poll(self, now: datetime | None = None) -> dict[str, Any]:
        """cron 進入點：抓取 + 清除過期快照。抓取失敗直接 raise（cron log 看得到）。"""
        now = as_utc(now or datetime.now(UTC))
        ingest = self.ingest(self._er().fetch(), now=now)
        purged = self._snapshots.purge_before(now - self._retention)
        return {**ingest, "purged": purged}

    def ingest(self, feed: ErFeed, *, now: datetime | None = None) -> dict[str, Any]:
        now = as_utc(now or feed.fetched_at)
        rows = [
            {
                "nhi_hospital_id": r.nhi_hospital_id,
                "hospital_name": r.hospital_name,
                "area_code": r.area_code,
                "hosp_type": r.hosp_type,
                "full_119": r.full_119,
                "wait_see": r.wait_see,
                "wait_bed": r.wait_bed,
                "wait_admit": r.wait_admit,
                "wait_icu": r.wait_icu,
                "reported_at": as_utc(r.reported_at),
                "fetched_at": now,
            }
            for r in feed.reports
        ]
        added = self._snapshots.add_missing(rows)
        return {"fetched": len(rows), "added": added, "sysdate": feed.sysdate}

    # ---- 查詢 ----

    def board(
        self, *, area_code: str | None = None, refresh_if_stale: bool = True
    ) -> dict[str, Any]:
        """某縣市（或全國）每家醫院的最新狀況 + 燈號，依餘裕排序（LIFF 看板與 AI 脈絡用）。"""
        if area_code is not None and area_code not in rules.AREA_NAMES:
            raise ValidationError("不支援的地區代碼", details={"area_code": area_code})
        now = datetime.now(UTC)
        latest_at = self._snapshots.latest_reported_at()
        stale = latest_at is None or now - latest_at > self._stale
        cooled_down = (
            self._last_refresh_attempt is None
            or now - self._last_refresh_attempt >= REFRESH_COOLDOWN
        )
        if stale and refresh_if_stale and cooled_down:
            self._last_refresh_attempt = now
            try:
                self.ingest(self._er().fetch(), now=now)
                latest_at = self._snapshots.latest_reported_at()
                stale = latest_at is None or now - latest_at > self._stale
            except ExternalServiceError:
                logger.warning("On-demand ER refresh failed; serving cached snapshots")
        rows = sorted(self._decorated_latest(now, area_code=area_code), key=rules.sort_key)
        return {
            "area_code": area_code,
            "area_name": rules.AREA_NAMES.get(area_code) if area_code else None,
            "reported_at": latest_at.isoformat() if latest_at else None,
            "reported_at_label": _tw_label(latest_at) if latest_at else None,
            "stale": stale,
            "hospitals": rows,
            "note": rules.DATA_SOURCE_NOTE,
        }

    def board_for_location(
        self, lat: float, lng: float, *, address: str | None = None, limit: int = NEARBY_LIMIT
    ) -> dict[str, Any] | None:
        """以使用者位置為準：有座標的重度級醫院依距離排序，推薦最近且未壅塞的一家。

        沒有任何急診即時資料（API 抓不到、快照是空的）時回 `unavailable=True`；
        有資料但院所主檔完全沒有座標時回 None（呼叫端提示尚未匯入院所座標）。
        """
        board = self.board()
        rows = []
        for h in board["hospitals"]:
            if h.get("lat") is None or h.get("lng") is None:
                continue
            rows.append({**h, "distance_km": round(haversine_km(lat, lng, h["lat"], h["lng"]), 1)})
        # 地址可能是英文或空白（LINE 依手機語系給），抓不到就用座標反推鄉鎮市區
        city, district = parse_address(address)
        if city is None:
            city, district = nearest_district(lat, lng) or (None, None)
        if not board["hospitals"]:
            return {
                **board,
                "mode": "distance",
                "origin": {"lat": lat, "lng": lng},
                "area_code": rules.AREA_CODES.get(city) if city else None,
                "area_name": city,
                "district": district,
                "recommended": None,
                "all_congested": False,
                "unavailable": True,
            }
        if not rows:
            return None
        rows.sort(key=lambda h: (h["distance_km"], rules.sort_key(h)))
        nearest = rows[:limit]
        recommended = next((h for h in nearest if h["level"] != rules.LEVEL_CRITICAL), None)
        if city is None:
            city = nearest[0]["area_name"]
        return {
            **board,
            "mode": "distance",
            "origin": {"lat": lat, "lng": lng},
            "area_code": rules.AREA_CODES.get(city),
            "area_name": city,
            "district": district,
            "hospitals": nearest,
            "recommended": recommended,
            "all_congested": recommended is None,
            "unavailable": False,
        }

    def nearby_clinics(
        self,
        city: str,
        district: str | None,
        *,
        lat: float | None = None,
        lng: float | None = None,
        department: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """非急症的替代：找同一鄉鎮市區（沒有就退到同縣市）的診所 / 地區 / 區域醫院。

        院所主檔裡的診所大多**沒有座標**（衛福部基本資料沒提供），所以先用地址比對鄉鎮市區，
        有座標的才算距離、並再補上 10 km 內有座標的院所；排序：層級小的優先（診所 → 地區 → 區域）
        → 距離近的優先（沒座標排後）→ 名稱。`department`（AI 依症狀建議的科別）有值時只留有該科
        的院所（對到該科本身的排在「西醫一般科」前面），一家都沒有再放寬成不限科別
        （回傳 `department_matched=False`）。
        """
        terms = rules.department_terms(department)
        result = self._clinic_pool(city, district, lat, lng, terms)
        matched = bool(terms) and bool(result["hospitals"])
        if terms and not matched:
            result = self._clinic_pool(city, district, lat, lng, ())

        def term_rank(h: dict[str, Any]) -> int:
            departments = h.get("departments") or ""
            return next((i for i, t in enumerate(terms) if t in departments), len(terms))

        rows = sorted(
            result["hospitals"],
            key=lambda h: (
                CLINIC_TIERS.index(h.get("capacity_tier")),
                term_rank(h),
                h.get("distance_km") if h.get("distance_km") is not None else float("inf"),
                h["name"],
            ),
        )
        return {
            **result,
            "hospitals": rows[:limit],
            "department": department,
            "department_terms": list(terms),
            "department_matched": matched,
        }

    def _clinic_pool(
        self,
        city: str,
        district: str | None,
        lat: float | None,
        lng: float | None,
        departments: tuple[str, ...],
    ) -> dict[str, Any]:
        rows = self._hospitals.search_nearby(
            city=city, district=district, tiers=CLINIC_TIERS, departments=departments, limit=50
        )
        scope = "district" if rows and district else "city"
        if not rows and district:
            rows = self._hospitals.search_nearby(
                city=city, district=None, tiers=CLINIC_TIERS, departments=departments, limit=50
            )
        pool = {h["id"]: h for h in rows}
        if lat is not None and lng is not None:
            for h in pool.values():
                if h.get("lat") is not None and h.get("lng") is not None:
                    h["distance_km"] = round(haversine_km(lat, lng, h["lat"], h["lng"]), 1)
            for h in self._hospitals.list_with_coords(tiers=CLINIC_TIERS, departments=departments):
                if h["id"] in pool:
                    continue
                distance = haversine_km(lat, lng, h["lat"], h["lng"])
                if distance <= CLINIC_RADIUS_KM:
                    pool[h["id"]] = {**h, "distance_km": round(distance, 1)}
            scope = "nearby"
        return {
            "city": city,
            "district": district,
            "scope": scope,
            "hospitals": list(pool.values()),
        }

    # ---- helpers ----

    def _decorated_latest(
        self, now: datetime, *, area_code: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self._snapshots.latest(area_code=area_code)
        # 只看最近一輪回報：某醫院若從 API 消失（或停止回報），舊快照不再當成現況
        newest = max((_parse_dt(r["reported_at"]) for r in rows), default=None)
        if newest is not None:
            cutoff = newest - 2 * self._stale
            rows = [r for r in rows if (_parse_dt(r["reported_at"]) or cutoff) >= cutoff]
        averages = self._snapshots.average_wait_admit(since=now - timedelta(hours=24))
        linked = self._hospitals.get_many([r["nhi_hospital_id"] for r in rows])
        result = []
        for row in rows:
            item = rules.decorate(row, averages.get(row["nhi_hospital_id"]))
            hospital = linked.get(row["nhi_hospital_id"])
            item["address"] = hospital.get("address") if hospital else None
            item["phone"] = hospital.get("contact_phone") if hospital else None
            item["lat"] = hospital.get("lat") if hospital else None
            item["lng"] = hospital.get("lng") if hospital else None
            item["reported_at_label"] = _tw_label(_parse_dt(row["reported_at"]))
            result.append(item)
        return result


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    try:
        return as_utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _tw_label(value: datetime | None) -> str:
    return value.astimezone(TAIPEI).strftime("%m/%d %H:%M") if value else ""
