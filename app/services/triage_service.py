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
from app.domain.assistant_schema import ClinicPicks
from app.integrations.nhi_er import ErFeed
from app.repositories.hospital_repository import HospitalRepository
from app.repositories.reminder_repository import as_utc
from app.repositories.triage_repository import SnapshotRepository
from app.services.pharmacy_service import parse_address
from app.utils.clock import TAIPEI
from app.utils.geo import district_centroid, haversine_km, nearest_district

logger = logging.getLogger(__name__)

CLINIC_TIERS = ("clinic", "district", "regional")
NEARBY_LIMIT = 8  # 依距離找醫院時列出幾家
CLINIC_RADIUS_KM = 10  # 找附近診所的距離上限
REFRESH_COOLDOWN = timedelta(seconds=60)
CLINIC_CANDIDATE_LIMIT = 600  # 交給模型挑選的候選上限（診所最多的鄉鎮市區約 500 家）
MAX_SYMPTOM_CHARS = 100
TIER_LABELS_SHORT = {"clinic": "診所", "district": "地區醫院", "regional": "區域醫院"}

CLINIC_PICK_PROMPT = """你是醫療照護 LINE 機器人的「診所挑選」元件。使用者有非急症的不適，
請從【候選院所】挑出適合就近看診的院所，只回編號。

規則：
1. 只能挑清單裡的編號，最多 {limit} 家，依適合程度排序；沒有適合的就回空清單，不要硬湊。
2. 只依清單上的「名稱」與「登記科別」（有才會列出）判斷看不看得了這個症狀 / 建議科別，
   不要用你對特定院所的印象。名稱與登記科別都看不出看什麼科的院所不要挑。
3. 排除與症狀無關的院所：醫美 / 美容 / 整形、牙科、中醫、眼科等其他專科
   （建議科別就是該科時除外）。名稱像醫美（美型、美學、時尚、Beauty…）的即使登記了
   家庭醫學科或西醫一般科也不要挑。
4. 非急症以診所優先，其次地區醫院、區域醫院；同樣適合時挑距離近的。
5. 症狀與建議科別都沒有時，挑一般看診的院所（家庭醫學科、內科、一般科診所）。
"""


class TriageService:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        hospitals: HospitalRepository,
        er_client: Callable[[], Any],
        *,
        llm: Callable[[], Any] | None = None,
        liff_id: str,
        stale_minutes: int = 30,
        retention_days: int = 30,
    ) -> None:
        self._snapshots = snapshots
        self._hospitals = hospitals
        self._er = er_client
        self._llm = llm  # 新增：依症狀挑診所用
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
        self,
        lat: float,
        lng: float,
        *,
        address: str | None = None,
        department: str | None = None,
        limit: int = NEARBY_LIMIT,
    ) -> dict[str, Any] | None:
        """以使用者位置為準：有座標的重度級醫院依距離排序，推薦最近且未壅塞的一家。

        修改：`department`（AI 依症狀建議的科別）有值時，每家標上 `department_matched`，
        推薦優先給「最近、未壅塞、且有該科」的醫院；都沒有該科再退回不限科別（`department_matched=False`）。
        沒有任何急診即時資料（API 抓不到、快照是空的）時回 `unavailable=True`；
        有資料但院所主檔既沒有座標、地址也認不出鄉鎮市區時回 None。
        """
        board = self.board()
        terms = rules.department_terms(department)
        rows = []
        for h in board["hospitals"]:
            # 修改：院所主檔沒有座標時，用地址所在鄉鎮市區的中心點估距離（`distance_approx`），
            # 不因為沒匯入座標就整個不能推薦；估的座標不寫回 lat / lng（不能拿來導航）
            has_coords = h.get("lat") is not None and h.get("lng") is not None
            point = (h["lat"], h["lng"]) if has_coords else None
            approx = point is None
            if approx:
                point = district_centroid(*parse_address(h.get("address")))
            if point is None:
                continue
            departments = h.get("departments") or ""
            rows.append(
                {
                    **h,
                    "distance_km": round(haversine_km(lat, lng, point[0], point[1]), 1),
                    "distance_approx": approx,
                    "department_matched": bool(terms) and any(t in departments for t in terms),
                }
            )
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
        open_rows = [h for h in nearest if h["level"] != rules.LEVEL_CRITICAL]
        # 有科別時先找「有該科且未壅塞」的最近一家；沒有再退回不限科別
        recommended = next((h for h in open_rows if h["department_matched"]), None) or next(
            iter(open_rows), None
        )
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
            "department": department,
            "department_matched": bool(recommended and recommended["department_matched"]),
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
        的院所（對到該科本身的排在「西醫一般科」前面）。
        修改：一家都沒有時**不再放寬成不限科別**，回空清單 + `department_matched=False`
        （院所主檔沒匯入科別時，放寬後只會照名稱列出不相干的診所，肚子痛卻推薦醫美診所）。
        """
        terms = rules.department_terms(department)
        result = self._clinic_pool(city, district, lat, lng, terms)
        matched = bool(terms) and bool(result["hospitals"])

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

    def recommend_clinics(
        self,
        city: str,
        district: str | None,
        *,
        lat: float | None = None,
        lng: float | None = None,
        symptom: str | None = None,
        department: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """依症狀推薦診所：同鄉鎮市區的院所（不先用科別過濾）交給模型，依名稱 / 登記科別挑出適合的。

        院所主檔的科別常常是空的，而且醫美診所也會登記家醫科，光用科別欄位比對不準；
        模型只回候選編號（`ClinicPicks`），院所資料由這裡依編號取回。回傳多一個 `selected_by="ai"`。
        沒有症狀也沒有科別（單純「附近診所」）時模型挑一般看診的院所；
        模型不可用時退回 `nearby_clinics()` 的科別比對。
        """
        symptom = (symptom or "").strip()[:MAX_SYMPTOM_CHARS]

        def rule_based() -> dict[str, Any]:
            return self.nearby_clinics(
                city, district, lat=lat, lng=lng, department=department, limit=limit
            )

        if self._llm is None:
            return rule_based()
        pool = self._clinic_pool(city, district, lat, lng, (), limit=CLINIC_CANDIDATE_LIMIT)
        candidates = sorted(
            pool["hospitals"],
            key=lambda h: (
                h.get("distance_km") if h.get("distance_km") is not None else float("inf"),
                h["name"],
            ),
        )[:CLINIC_CANDIDATE_LIMIT]
        terms = rules.department_terms(department)
        chosen: list[dict[str, Any]] = []
        if candidates:
            try:
                result = self._llm().complete_structured(
                    self._clinic_pick_prompt(symptom, department, terms, candidates),
                    ClinicPicks,
                    system=CLINIC_PICK_PROMPT.replace("{limit}", str(limit)),
                )
            except ExternalServiceError as exc:
                logger.warning(
                    "Clinic picking unavailable (%s); using department match", exc.message
                )
                return rule_based()
            # 編號從 1 起算；超出範圍或重複的編號丟掉
            for index in dict.fromkeys(result.picks):
                if 1 <= index <= len(candidates):
                    chosen.append(candidates[index - 1])
        return {
            **pool,
            "hospitals": chosen[:limit],
            "department": department,
            "department_terms": list(terms),
            "department_matched": bool(chosen),
            "selected_by": "ai",
        }

    @staticmethod
    def _clinic_pick_prompt(
        symptom: str,
        department: str | None,
        terms: tuple[str, ...],
        candidates: list[dict[str, Any]],
    ) -> str:
        lines = []
        for number, h in enumerate(candidates, start=1):
            parts = [str(number), h["name"], TIER_LABELS_SHORT.get(h.get("capacity_tier"), "院所")]
            if h.get("departments"):
                parts.append(f"登記科別 {h['departments']}")
            if h.get("distance_km") is not None:
                parts.append(f"{h['distance_km']} km")
            lines.append("｜".join(parts))
        same = f"（院所登記用詞：{'、'.join(terms)}）" if terms else ""
        return (
            f"【症狀】{symptom or '未說明'}\n"
            f"【建議科別】{(department or '未指定') + same}\n"
            "【候選院所】編號｜名稱｜層級｜登記科別｜距離\n" + "\n".join(lines)
        )

    def _clinic_pool(
        self,
        city: str,
        district: str | None,
        lat: float | None,
        lng: float | None,
        departments: tuple[str, ...],
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        rows = self._hospitals.search_nearby(
            city=city, district=district, tiers=CLINIC_TIERS, departments=departments, limit=limit
        )
        scope = "district" if rows and district else "city"
        if not rows and district:
            rows = self._hospitals.search_nearby(
                city=city, district=None, tiers=CLINIC_TIERS, departments=departments, limit=limit
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
            item["departments"] = hospital.get("departments") if hospital else None  # 新增
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
