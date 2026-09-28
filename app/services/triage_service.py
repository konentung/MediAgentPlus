"""急診動態分流：定時抓健保署急診即時資料 → 快照 → 壅塞評估 → 分流建議 → 通知。

排程：`flask triage poll` 由 cron 每 15 分鐘執行（或 `--loop` 常駐）。每次 poll：
  1. `ingest()`   抓 API、寫入 `er_snapshots`（同一回報時間不重複）、清掉超過保留天數的舊快照
  2. `evaluate()` 每個縣市：有「壅塞」燈號的醫院 → 沒有進行中的建議就開立、有就更新；
                  沒有壅塞醫院了 → 解除
  3. 開立 / 新增壅塞醫院 / 超過重複通知間隔 / 解除 → 通知訂閱者（LINE push、Webhook）

LINE「找醫院」優先用使用者分享的經緯度 `board_for_location()`
（依距離排序、推薦最近且未壅塞的醫院），沒有座標時退回縣市看板 `board()`。
院所座標來自 `hospitals.lat / lng`（種子檔 `data/er_hospitals.csv`，`flask hospital import` 匯入）。
LINE「找醫院」與 LIFF 看板讀 `board()`：快照過舊（超過 TRIAGE_STALE_MINUTES）會嘗試即時抓一次，
但**不**做建議評估與通知（只在 cron 內做，避免 webhook 請求中觸發推播）。
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.exceptions import (
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.domain import triage_rules as rules
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.integrations.nhi_er import ErFeed
from app.line.messages import hospital as hospital_messages
from app.repositories.hospital_repository import HospitalRepository
from app.repositories.reminder_repository import as_utc
from app.repositories.triage_repository import (
    AdvisoryRepository,
    SnapshotRepository,
    SubscriptionRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.pharmacy_service import parse_address
from app.utils.clock import TAIPEI
from app.utils.crypto import FieldCipher
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

SUBSCRIPTION_KINDS = ("line", "webhook", "api")
EVENT_OPENED, EVENT_UPDATED, EVENT_RESOLVED, EVENT_TEST = "opened", "updated", "resolved", "test"
CLINIC_TIERS = ("clinic", "district", "regional")
MAX_ALTERNATIVES = 5
NEARBY_LIMIT = 8  # 依距離找醫院時列出幾家
ALTERNATIVE_RADIUS_KM = 50  # 分流建議：離壅塞醫院多遠內的醫院可當替代
CLINIC_RADIUS_KM = 10  # 找附近診所的距離上限
REFRESH_COOLDOWN = timedelta(seconds=60)


class TriageService:
    def __init__(
        self,
        snapshots: SnapshotRepository,
        advisories: AdvisoryRepository,
        subscriptions: SubscriptionRepository,
        hospitals: HospitalRepository,
        users: UserRepository,
        cipher: FieldCipher,
        er_client: Callable[[], Any],
        webhook_client: Callable[[], Any],
        line: Callable[[], Any],
        *,
        liff_id: str,
        stale_minutes: int = 30,
        renotify_minutes: int = 120,
        retention_days: int = 30,
    ) -> None:
        self._snapshots = snapshots
        self._advisories = advisories
        self._subscriptions = subscriptions
        self._hospitals = hospitals
        self._users = users
        self._cipher = cipher
        self._er = er_client
        self._webhook = webhook_client
        self._line = line
        self._liff_id = liff_id
        self._stale = timedelta(minutes=stale_minutes)
        self._renotify = timedelta(minutes=renotify_minutes)
        self._retention = timedelta(days=retention_days)
        # 即時補抓的冷卻：API 故障時不讓每個請求都等到逾時
        self._last_refresh_attempt: datetime | None = None

    def liff_url(self, path: str = "/er") -> str:
        return f"https://liff.line.me/{self._liff_id}{path}"

    # ---- 排程：抓取 + 評估 + 通知 ----

    def poll(self, now: datetime | None = None) -> dict[str, Any]:
        """cron 進入點。抓取失敗會直接 raise（cron log 看得到），評估與通知照常以既有快照進行。"""
        now = as_utc(now or datetime.now(UTC))
        ingest = self.ingest(self._er().fetch(), now=now)
        purged = self._snapshots.purge_before(now - self._retention)
        events = self.evaluate(now)
        return {**ingest, "purged": purged, **events}

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

    def evaluate(self, now: datetime | None = None) -> dict[str, int]:
        """逐縣市評估壅塞狀況，維護分流建議的生命週期並發送通知。"""
        now = as_utc(now or datetime.now(UTC))
        board = self._decorated_latest(now)
        by_area: dict[str, list[dict[str, Any]]] = {}
        for row in board:
            by_area.setdefault(row["area_code"], []).append(row)

        summary = {"opened": 0, "updated": 0, "resolved": 0, "notified": 0}
        for area_code, hospitals in by_area.items():
            congested = [h for h in hospitals if h["level"] == rules.LEVEL_CRITICAL]
            current = self._advisories.get_open(area_code)
            if congested:
                alternatives = self._alternatives(area_code, by_area, congested)
                report = build_report(area_code, congested, alternatives, now)
                values = {
                    "congested": [_compact(h) for h in congested],
                    "alternatives": alternatives,
                    "report": report,
                    "snapshot_reported_at": _latest_time(hospitals),
                }
                if current is None:
                    advisory = self._advisories.create(
                        area_code=area_code, status="open", opened_at=now, **values
                    )
                    summary["opened"] += 1
                    summary["notified"] += self._notify(advisory, EVENT_OPENED, now)
                else:
                    known = {h["nhi_hospital_id"] for h in current.get("congested") or []}
                    new_ids = {h["nhi_hospital_id"] for h in congested} - known
                    advisory = self._advisories.update(current["id"], **values)
                    summary["updated"] += 1
                    last = _parse_dt(current.get("last_notified_at"))
                    if new_ids or last is None or now - last >= self._renotify:
                        summary["notified"] += self._notify(advisory, EVENT_UPDATED, now)
            elif current is not None:
                advisory = self._advisories.update(
                    current["id"], status="resolved", resolved_at=now
                )
                summary["resolved"] += 1
                summary["notified"] += self._notify(advisory, EVENT_RESOLVED, now)
        return summary

    # ---- 查詢：看板 / 找醫院 ----

    def board(
        self, *, area_code: str | None = None, refresh_if_stale: bool = True
    ) -> dict[str, Any]:
        """某縣市（或全國）每家醫院的最新狀況 + 燈號，依餘裕排序。"""
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

        院所主檔完全沒有座標時回 None（呼叫端退回縣市看板）。
        """
        board = self.board()
        rows = []
        for h in board["hospitals"]:
            if h.get("lat") is None or h.get("lng") is None:
                continue
            rows.append({**h, "distance_km": round(haversine_km(lat, lng, h["lat"], h["lng"]), 1)})
        if not rows:
            return None
        rows.sort(key=lambda h: (h["distance_km"], rules.sort_key(h)))
        nearest = rows[:limit]
        recommended = next((h for h in nearest if h["level"] != rules.LEVEL_CRITICAL), None)
        city, district = parse_address(address)
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
        }

    def board_for_address(self, address: str | None) -> dict[str, Any] | None:
        """LINE 位置訊息 / 住址 → 縣市看板；無法判斷縣市回 None。"""
        city, district = parse_address(address)
        if city is None:
            return None
        area_code = rules.AREA_CODES.get(city)
        if area_code is None:
            return None
        board = self.board(area_code=area_code)
        board["district"] = district
        return board

    def nearby_clinics(
        self,
        city: str,
        district: str | None,
        *,
        lat: float | None = None,
        lng: float | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """非急症的替代：有座標就依距離找診所 / 地區 / 區域醫院，否則同區（退到同縣市）比對。"""
        if lat is not None and lng is not None:
            with_coords = []
            for h in self._hospitals.list_with_coords(tiers=CLINIC_TIERS):
                distance = haversine_km(lat, lng, h["lat"], h["lng"])
                if distance <= CLINIC_RADIUS_KM:
                    with_coords.append({**h, "distance_km": round(distance, 1)})
            if with_coords:
                with_coords.sort(key=lambda h: h["distance_km"])
                return {
                    "city": city,
                    "district": district,
                    "scope": "distance",
                    "hospitals": with_coords[:limit],
                }
        rows = self._hospitals.search_nearby(
            city=city, district=district, tiers=CLINIC_TIERS, limit=limit
        )
        scope = "district" if rows and district else "city"
        if not rows and district:
            rows = self._hospitals.search_nearby(
                city=city, district=None, tiers=CLINIC_TIERS, limit=limit
            )
        return {"city": city, "district": district, "scope": scope, "hospitals": rows}

    def hospital_history(self, nhi_hospital_id: str, *, hours: int = 24) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(hours=max(1, min(hours, 24 * 7)))
        return self._snapshots.history(nhi_hospital_id, since=since)

    def list_advisories(
        self, *, status: str | None = None, area_code: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return [
            self._advisory_public(a)
            for a in self._advisories.list(status=status, area_code=area_code, limit=limit)
        ]

    def stats(self) -> dict[str, Any]:
        latest_at = self._snapshots.latest_reported_at()
        return {
            "snapshots": self._snapshots.count(),
            "reported_at": latest_at.isoformat() if latest_at else None,
            "stale": latest_at is None or datetime.now(UTC) - latest_at > self._stale,
            "open_advisories": len(self._advisories.list(status="open", limit=100)),
        }

    # ---- 訂閱管理（院方後台）----

    def list_subscriptions(self, actor: Principal) -> list[dict[str, Any]]:
        scope = AccessPolicy.hospital_scope(actor)
        return [self._subscription_public(s) for s in self._subscriptions.list(hospital_id=scope)]

    def create_subscription(self, actor: Principal, body: dict[str, Any]) -> dict[str, Any]:
        """建立訂閱。webhook / api 類型會產生一次性顯示的 secret（回傳值含 `secret`）。"""
        kind = str(body.get("kind") or "").strip()
        if kind not in SUBSCRIPTION_KINDS:
            raise ValidationError("kind 須為 line / webhook / api")
        label = str(body.get("label") or "").strip()
        if not label or len(label) > 100:
            raise ValidationError("請填寫接收單位名稱（100 字內）")
        values: dict[str, Any] = {
            "kind": kind,
            "label": label,
            "area_codes": self._clean_areas(body.get("area_codes")),
            "hospital_id": None if actor.role is Role.SUPER_ADMIN else actor.hospital_id,
            "created_by": actor.user_id,
            "active": True,
        }
        if actor.role is not Role.SUPER_ADMIN and not values["hospital_id"]:
            raise AuthorizationError("你的帳號尚未指派所屬院所，請聯絡管理員")
        plain_secret: str | None = None
        if kind == "line":
            user_id = str(body.get("user_id") or "").strip()
            user = self._users.get_by_id(user_id) if user_id else None
            if user is None or not user.line_user_id:
                raise ValidationError("找不到這位 LINE 使用者，請先請對方加入官方帳號")
            values["user_id"] = user.id
        else:
            if kind == "webhook":
                url = str(body.get("webhook_url") or "").strip()
                if not url.startswith("https://") or len(url) > 500:
                    raise ValidationError("Webhook 網址必須是 https:// 開頭")
                values["webhook_url"] = url
            plain_secret = secrets.token_urlsafe(32)
            values["secret_encrypted"] = self._cipher.encrypt(plain_secret)
            values["secret_fingerprint"] = self._cipher.fingerprint(plain_secret)
        created = self._subscription_public(self._subscriptions.create(**values))
        if plain_secret:
            created["secret"] = plain_secret  # 只在建立時回一次
        return created

    def update_subscription(
        self, actor: Principal, subscription_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self._get_subscription(actor, subscription_id)
        changes: dict[str, Any] = {}
        if "label" in body:
            label = str(body["label"] or "").strip()
            if not label or len(label) > 100:
                raise ValidationError("接收單位名稱不可為空（100 字內）")
            changes["label"] = label
        if "area_codes" in body:
            changes["area_codes"] = self._clean_areas(body["area_codes"])
        if "active" in body:
            changes["active"] = bool(body["active"])
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        return self._subscription_public(self._subscriptions.update(subscription_id, **changes))

    def delete_subscription(self, actor: Principal, subscription_id: str) -> None:
        self._get_subscription(actor, subscription_id)
        self._subscriptions.delete(subscription_id)

    def send_test(self, actor: Principal, subscription_id: str) -> dict[str, Any]:
        """對單一訂閱送測試通知（用最近一筆建議，沒有就用範例）。"""
        subscription = self._get_subscription(actor, subscription_id)
        latest = self._advisories.list(limit=1)
        advisory = latest[0] if latest else _sample_advisory()
        ok = self._deliver(subscription, advisory, EVENT_TEST, datetime.now(UTC))
        return {"delivered": ok, "subscription": self._subscription_public(subscription)}

    def advisories_for_api_key(self, api_key: str | None) -> list[dict[str, Any]]:
        """外部系統（119 勤指中心）以 API key 主動拉取進行中的建議。"""
        subscription = (
            self._subscriptions.get_by_fingerprint(self._cipher.fingerprint(api_key))
            if api_key
            else None
        )
        if subscription is None or not subscription["active"]:
            raise AuthorizationError("API key 無效")
        areas = set(subscription.get("area_codes") or [])
        return [
            self._advisory_public(a)
            for a in self._advisories.list(status="open", limit=100)
            if not areas or a["area_code"] in areas
        ]

    # ---- 內部：通知 ----

    def _notify(self, advisory: dict[str, Any], event: str, now: datetime) -> int:
        delivered = 0
        for subscription in self._subscriptions.list_active():
            areas = subscription.get("area_codes") or []
            if areas and advisory["area_code"] not in areas:
                continue
            if subscription["kind"] == "api":
                continue  # 拉取型不推送
            if self._deliver(subscription, advisory, event, now):
                delivered += 1
        self._advisories.update(
            advisory["id"], last_notified_at=now, notify_count=advisory["notify_count"] + 1
        )
        return delivered

    def _deliver(
        self, subscription: dict[str, Any], advisory: dict[str, Any], event: str, now: datetime
    ) -> bool:
        public = self._advisory_public(advisory)
        try:
            if subscription["kind"] == "line":
                user = self._users.get_by_id(subscription["user_id"] or "")
                if user is None or not user.line_user_id or not user.push_enabled:
                    raise ExternalServiceError("LINE 使用者不存在或已封鎖官方帳號")
                self._line().push(
                    user.line_user_id,
                    hospital_messages.advisory_messages(public, event, self.liff_url()),
                )
            elif subscription["kind"] == "webhook":
                self._webhook().post(
                    subscription["webhook_url"],
                    {"event": event, "advisory": public, "generated_at": now.isoformat()},
                    secret=self._cipher.decrypt(subscription["secret_encrypted"]),
                    event=event,
                )
            else:
                return False
        except Exception as exc:  # noqa: BLE001 - 單一訂閱失敗不影響其他人
            logger.exception("Triage notification failed for subscription %s", subscription["id"])
            self._subscriptions.update(subscription["id"], last_error=str(exc)[:300])
            return False
        self._subscriptions.update(
            subscription["id"],
            last_notified_at=now,
            last_error=None,
            notify_count=subscription["notify_count"] + 1,
        )
        return True

    # ---- 內部：資料整理 ----

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

    @staticmethod
    def _alternatives(
        area_code: str,
        by_area: dict[str, list[dict[str, Any]]],
        congested: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """替代醫院。

        壅塞醫院有座標時：全國未壅塞且有座標的醫院，依「離壅塞醫院最近」排序
        （ALTERNATIVE_RADIUS_KM 內），
        多家壅塞時合併、取最近距離；沒有座標時退回：同縣市有餘裕 → 同縣市偏忙 → 鄰近縣市有餘裕。
        最多 MAX_ALTERNATIVES 家。
        """
        anchors = [
            h for h in (congested or []) if h.get("lat") is not None and h.get("lng") is not None
        ]
        if anchors:
            nearest: dict[str, dict[str, Any]] = {}
            for hospitals in by_area.values():
                for h in hospitals:
                    if h["level"] == rules.LEVEL_CRITICAL or h.get("lat") is None:
                        continue
                    for anchor in anchors:
                        distance = haversine_km(anchor["lat"], anchor["lng"], h["lat"], h["lng"])
                        if distance > ALTERNATIVE_RADIUS_KM:
                            continue
                        current = nearest.get(h["nhi_hospital_id"])
                        if current is None or distance < current["distance_km"]:
                            nearest[h["nhi_hospital_id"]] = {
                                **_compact(h),
                                "distance_km": round(distance, 1),
                                "distance_from": anchor["hospital_name"],
                            }
            picks = sorted(
                nearest.values(), key=lambda h: (h["distance_km"], rules.LEVEL_RANK[h["level"]])
            )
            if picks:
                return picks[:MAX_ALTERNATIVES]
        same = sorted(by_area.get(area_code, []), key=rules.sort_key)
        picks = [h for h in same if h["level"] == rules.LEVEL_NORMAL]
        picks += [h for h in same if h["level"] == rules.LEVEL_BUSY]
        for neighbor in rules.ADJACENT_AREAS.get(area_code, ()):
            picks += [
                h
                for h in sorted(by_area.get(neighbor, []), key=rules.sort_key)
                if h["level"] == rules.LEVEL_NORMAL
            ]
        return [_compact(h) for h in picks[:MAX_ALTERNATIVES]]

    def _get_subscription(self, actor: Principal, subscription_id: str) -> dict[str, Any]:
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None:
            raise NotFoundError("找不到這筆訂閱")
        if actor.role is not Role.SUPER_ADMIN and subscription.get(
            "hospital_id"
        ) != AccessPolicy.hospital_scope(actor):
            raise AuthorizationError("不得存取其他院所建立的訂閱")
        return subscription

    @staticmethod
    def _clean_areas(raw: Any) -> list[str]:
        if raw in (None, ""):
            return []
        if isinstance(raw, str):
            raw = [x for x in raw.replace("，", ",").split(",")]
        if not isinstance(raw, list):
            raise ValidationError("area_codes 須為地區代碼陣列")
        cleaned: list[str] = []
        for value in raw:
            code = str(value).strip()
            if not code:
                continue
            code = rules.AREA_CODES.get(code.replace("臺", "台"), code)
            if code not in rules.AREA_NAMES:
                raise ValidationError("不支援的地區代碼", details={"area_code": code})
            if code not in cleaned:
                cleaned.append(code)
        return cleaned

    @staticmethod
    def _advisory_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "area_code": row["area_code"],
            "area_name": rules.AREA_NAMES.get(row["area_code"], row["area_code"]),
            "status": row["status"],
            "congested": row.get("congested") or [],
            "alternatives": row.get("alternatives") or [],
            "report": row.get("report") or "",
            "snapshot_reported_at": row.get("snapshot_reported_at"),
            "opened_at": row.get("opened_at"),
            "resolved_at": row.get("resolved_at"),
            "last_notified_at": row.get("last_notified_at"),
            "notify_count": row.get("notify_count", 0),
        }

    def _subscription_public(self, row: dict[str, Any]) -> dict[str, Any]:
        user = self._users.get_by_id(row["user_id"]) if row.get("user_id") else None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "label": row["label"],
            "area_codes": row.get("area_codes") or [],
            "area_names": [rules.AREA_NAMES.get(c, c) for c in row.get("area_codes") or []],
            "user_id": row.get("user_id"),
            "user_display_name": user.display_name if user else None,
            "webhook_url": row.get("webhook_url"),
            "has_secret": bool(row.get("secret_encrypted")),
            "hospital_id": row.get("hospital_id"),
            "active": row["active"],
            "last_notified_at": row.get("last_notified_at"),
            "last_error": row.get("last_error"),
            "notify_count": row.get("notify_count", 0),
            "created_at": row.get("created_at"),
        }


# ---- 模組層工具 ----


def build_report(
    area_code: str,
    congested: list[dict[str, Any]],
    alternatives: list[dict[str, Any]],
    now: datetime,
) -> str:
    """純文字分流建議報告（LINE 文字、Webhook payload、後台皆用）。"""
    area = rules.AREA_NAMES.get(area_code, area_code)
    lines = [f"【急診分流建議】{area}　{_tw_label(now)}", "壅塞醫院："]
    lines += [f"・{_describe(h)}" for h in congested]
    if alternatives:
        lines.append("建議轉往（依餘裕排序）：")
        for h in alternatives:
            prefix = "" if h["area_code"] == area_code else f"{h['area_name']} "
            distance = (
                f"距{h['distance_from']} {h['distance_km']} km，"
                if h.get("distance_km") is not None
                else ""
            )
            lines.append(f"・{prefix}{distance}{_describe(h)}")
    else:
        lines.append("同縣市與鄰近縣市目前沒有明顯有餘裕的重度級醫院，請依 119 調度判斷。")
    lines.append(rules.DATA_SOURCE_NOTE)
    return "\n".join(lines)


def _describe(h: dict[str, Any]) -> str:
    flag = "，119 滿床通報" if h.get("full_119") else ""
    return (
        f"{h['hospital_name']}（{h['tier_label']}）等住院 {h['wait_admit']}、"
        f"等 ICU {h['wait_icu']}、等看診 {h['wait_see']}{flag}"
    )


def _compact(h: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "nhi_hospital_id",
        "hospital_name",
        "area_code",
        "area_name",
        "hosp_type",
        "tier_label",
        "level",
        "level_label",
        "full_119",
        "wait_see",
        "wait_admit",
        "wait_icu",
        "ratio_24h",
        "reported_at",
        "lat",
        "lng",
    )
    return {k: h.get(k) for k in keys}


def _latest_time(rows: list[dict[str, Any]]) -> datetime | None:
    times = [_parse_dt(r.get("reported_at")) for r in rows]
    times = [t for t in times if t]
    return max(times) if times else None


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


def _sample_advisory() -> dict[str, Any]:
    sample = {
        "nhi_hospital_id": "0000000000",
        "hospital_name": "範例醫院",
        "area_code": "01",
        "area_name": "台北市",
        "hosp_type": "1",
        "tier_label": "醫學中心",
        "level": "critical",
        "level_label": "壅塞",
        "full_119": True,
        "wait_see": 30,
        "wait_admit": 80,
        "wait_icu": 5,
        "ratio_24h": None,
        "reported_at": None,
    }
    alt = {**sample, "hospital_name": "範例區域醫院", "hosp_type": "2", "tier_label": "區域醫院"}
    alt.update(level="normal", level_label="尚有餘裕", full_119=False, wait_admit=3, wait_icu=0)
    now = datetime.now(UTC)
    return {
        "id": "test",
        "area_code": "01",
        "status": "open",
        "congested": [sample],
        "alternatives": [alt],
        "report": build_report("01", [sample], [alt], now),
        "snapshot_reported_at": now,
        "opened_at": now,
        "resolved_at": None,
        "last_notified_at": None,
        "notify_count": 0,
    }
