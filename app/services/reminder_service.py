"""服藥小幫手：從處方產生排程、CRUD、到期推播（`flask reminder run-due`）、病人回報。

推播失敗只記 log 並把該筆標成 failed，不讓整個 cron 中斷；同一人同一批多顆藥合併成一則訊息。
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.principal import Principal
from app.domain.reminder_rules import FREQUENCY_CODES, TIMING_CODES, default_times
from app.line.messages import reminder as reminder_messages
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.reminder_repository import DeliveryRepository, ScheduleRepository, as_utc
from app.repositories.user_repository import UserRepository
from app.utils.clock import TAIPEI, now_tw

logger = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SNOOZE_MINUTES = 30
RESPONSE_STATUSES = {"taken": "taken", "snooze": "snoozed", "skip": "skipped"}


class ReminderService:
    def __init__(
        self,
        schedules: ScheduleRepository,
        deliveries: DeliveryRepository,
        prescriptions: PrescriptionRepository,
        users: UserRepository,
        line: Callable[[], Any],
        *,
        liff_id: str,
        window_minutes: int = 15,
    ) -> None:
        self._schedules = schedules
        self._deliveries = deliveries
        self._prescriptions = prescriptions
        self._users = users
        self._line = line
        self._liff_id = liff_id
        self._window = timedelta(minutes=window_minutes)

    def liff_url(self, path: str = "/reminders") -> str:
        return f"https://liff.line.me/{self._liff_id}{path}"

    # ---- 從處方產生 ----

    def propose_from_prescription(
        self, principal: Principal, prescription_id: str
    ) -> list[dict[str, Any]]:
        prescription = self._prescriptions.get(prescription_id)
        if (
            prescription is None
            or prescription["user_id"] != principal.user_id
            or prescription["status"] == "discarded"
        ):
            raise NotFoundError("找不到這張處方")
        today = now_tw().date()
        drafts: list[dict[str, Any]] = []
        for item in prescription.get("items") or []:
            days = item.get("duration_days") or prescription.get("days_supply")
            drafts.append(
                {
                    "prescription_id": prescription_id,
                    "drug_name": item["drug_name"],
                    "dose_text": item.get("dose_text"),
                    "frequency_code": item.get("frequency_code") or "other",
                    "timing": item.get("timing"),
                    "times": default_times(
                        item.get("frequency_code") or "other", item.get("timing")
                    ),
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=days - 1)).isoformat() if days else None,
                    "category": item.get("category"),
                }
            )
        return drafts

    # ---- CRUD ----

    def create_many(self, principal: Principal, drafts: Any) -> list[dict[str, Any]]:
        if not isinstance(drafts, list) or not drafts:
            raise ValidationError("請至少提供一筆提醒")
        if len(drafts) > 30:
            raise ValidationError("一次最多建立 30 筆提醒")
        prepared = [self._clean(d, index) for index, d in enumerate(drafts, start=1)]
        return [self._schedules.create(user_id=principal.user_id, **values) for values in prepared]

    def list_own(self, principal: Principal) -> dict[str, Any]:
        schedules = self._schedules.list_for_user(principal.user_id)
        today = now_tw().date()
        start = datetime.combine(today, time.min, TAIPEI)
        deliveries = self._deliveries.list_for_schedules([s["id"] for s in schedules], since=start)
        by_schedule: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in deliveries:
            by_schedule[d["schedule_id"]].append(self._delivery_public(d))
        for s in schedules:
            s["today"] = sorted(by_schedule.get(s["id"], []), key=lambda d: d["scheduled_for"])
            s["status"] = self._status_label(s, today)
        return {"schedules": schedules, "today": today.isoformat()}

    def update(self, principal: Principal, schedule_id: str, body: dict[str, Any]) -> dict:
        self._get_own(principal, schedule_id)
        changes: dict[str, Any] = {}
        if "times" in body:
            changes["times"] = self._clean_times(body["times"], body.get("frequency_code"))
        if "end_date" in body:
            changes["end_date"] = _parse_date(body["end_date"])
        if "active" in body:
            changes["active"] = bool(body["active"])
        if "paused_until" in body:
            changes["paused_until"] = _parse_date(body["paused_until"])
        if "dose_text" in body:
            changes["dose_text"] = str(body["dose_text"] or "").strip()[:50] or None
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        return self._schedules.update(schedule_id, **changes)

    def pause(self, principal: Principal, schedule_id: str, days: int = 7) -> dict[str, Any]:
        self._get_own(principal, schedule_id)
        until = now_tw().date() + timedelta(days=days)
        return self._schedules.update(schedule_id, paused_until=until)

    def resume(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        self._get_own(principal, schedule_id)
        return self._schedules.update(schedule_id, paused_until=None, active=True)

    def delete(self, principal: Principal, schedule_id: str) -> None:
        self._get_own(principal, schedule_id)
        self._schedules.delete(schedule_id)

    def history(self, principal: Principal, days: int = 7) -> list[dict[str, Any]]:
        schedules = {s["id"]: s for s in self._schedules.list_for_user(principal.user_id)}
        since = now_tw() - timedelta(days=max(1, min(days, 90)))
        rows = self._deliveries.list_for_schedules(list(schedules), since=since)
        return [
            {**self._delivery_public(d), "drug_name": schedules[d["schedule_id"]]["drug_name"]}
            for d in rows
        ]

    # ---- 病人回報 ----

    def respond(self, principal: Principal, delivery_id: str, action: str) -> dict[str, Any]:
        status = RESPONSE_STATUSES.get(action)
        if status is None:
            raise ValidationError("不支援的回報動作")
        delivery = self._deliveries.get(delivery_id)
        schedule = self._schedules.get(delivery["schedule_id"]) if delivery else None
        if schedule is None or schedule["user_id"] != principal.user_id:
            raise NotFoundError("找不到這則提醒")
        now = datetime.now(UTC)
        changes: dict[str, Any] = {"status": status, "responded_at": now}
        if status == "snoozed":
            changes["snooze_until"] = now + timedelta(minutes=SNOOZE_MINUTES)
        updated = self._deliveries.update(delivery_id, **changes)
        return {**self._delivery_public(updated), "drug_name": schedule["drug_name"]}

    # ---- 排程推播 ----

    def run_due(self, now: datetime | None = None) -> dict[str, int]:
        """找出時間到的提醒並推播。設計成可重複執行：同一時段只會建立 / 送出一次。"""
        now = as_utc(now or now_tw())
        today = now.astimezone(TAIPEI).date()
        window_start = now - self._window
        due: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for schedule in self._schedules.list_due_on(today):
            for hhmm in schedule.get("times") or []:
                if not TIME_PATTERN.match(str(hhmm)):
                    continue
                hour, minute = (int(x) for x in hhmm.split(":"))
                scheduled = datetime.combine(today, time(hour, minute), TAIPEI)
                if window_start < scheduled <= now:
                    delivery = self._deliveries.create_if_absent(schedule["id"], scheduled)
                    if delivery:
                        due.append((delivery, schedule))

        for delivery in self._deliveries.list_snoozed_due(now):
            schedule = self._schedules.get(delivery["schedule_id"])
            if schedule and schedule["active"]:
                due.append((delivery, schedule))

        summary = {"sent": 0, "skipped": 0, "failed": 0, "refill_sent": 0}
        by_user: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for delivery, schedule in due:
            by_user[schedule["user_id"]].append((delivery, schedule))

        for user_id, batch in by_user.items():
            user = self._users.get_by_id(user_id)
            if user is None or not user.line_user_id or not user.push_enabled:
                for delivery, _ in batch:
                    self._deliveries.update(delivery["id"], status="skipped")
                summary["skipped"] += len(batch)
                continue
            items = [
                {
                    "delivery_id": delivery["id"],
                    "drug_name": schedule["drug_name"],
                    "dose_text": schedule.get("dose_text"),
                    "time_label": _tw_hhmm(delivery["scheduled_for"]),
                }
                for delivery, schedule in batch
            ]
            messages = reminder_messages.due_messages(items, self.liff_url())
            ok = self._push(user.line_user_id, messages)
            for delivery, _ in batch:
                self._deliveries.update(
                    delivery["id"],
                    status="sent" if ok else "failed",
                    sent_at=now if ok else None,
                    snooze_until=None,
                )
            summary["sent" if ok else "failed"] += len(batch)

        summary["refill_sent"] = self._run_refill(today)
        return summary

    def _run_refill(self, today: date) -> int:
        sent = 0
        for prescription in self._prescriptions.list_refill_due(today):
            user = self._users.get_by_id(prescription["user_id"])
            if user and user.line_user_id and user.push_enabled:
                messages = reminder_messages.refill_messages(
                    prescription, self.liff_url(f"/prescriptions/{prescription['id']}")
                )
                if self._push(user.line_user_id, messages):
                    sent += 1
            # 無論成功與否都標記，避免每 5 分鐘重試轟炸；失敗有 log 可查
            self._prescriptions.update(prescription["id"], refill_reminded_at=datetime.now(UTC))
        return sent

    def _push(self, line_user_id: str, messages: list) -> bool:
        try:
            self._line().push(line_user_id, messages)
            return True
        except Exception:  # noqa: BLE001 - LINE API 失敗不能讓 cron 中斷
            logger.exception("Reminder push failed for %s", line_user_id)
            return False

    # ---- helpers ----

    def _get_own(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        schedule = self._schedules.get(schedule_id)
        if schedule is None or schedule["user_id"] != principal.user_id:
            raise NotFoundError("找不到這筆提醒")
        return schedule

    def _clean(self, draft: Any, index: int) -> dict[str, Any]:
        if not isinstance(draft, dict):
            raise ValidationError(f"第 {index} 筆格式錯誤")
        drug_name = str(draft.get("drug_name") or "").strip()
        if not drug_name or len(drug_name) > 150:
            raise ValidationError(f"第 {index} 筆缺少藥名或藥名過長")
        freq = draft.get("frequency_code") or "other"
        if freq not in FREQUENCY_CODES:
            raise ValidationError(f"第 {index} 筆頻率不合法", details={"allowed": FREQUENCY_CODES})
        timing = draft.get("timing") or None
        if timing is not None and timing not in TIMING_CODES:
            raise ValidationError(f"第 {index} 筆服用時機不合法")
        start = _parse_date(draft.get("start_date")) or now_tw().date()
        end = _parse_date(draft.get("end_date"))
        if end is not None and end < start:
            raise ValidationError(f"第 {index} 筆結束日不可早於開始日")
        return {
            "prescription_id": draft.get("prescription_id") or None,
            "drug_name": drug_name,
            "dose_text": str(draft.get("dose_text") or "").strip()[:50] or None,
            "frequency_code": freq,
            "timing": timing,
            "times": self._clean_times(draft.get("times"), freq),
            "start_date": start,
            "end_date": end,
            "active": True,
        }

    @staticmethod
    def _clean_times(raw: Any, frequency_code: str | None) -> list[str]:
        if not isinstance(raw, list):
            raise ValidationError("times 必須是 HH:MM 陣列")
        times: list[str] = []
        for value in raw:
            text = str(value).strip()
            if not TIME_PATTERN.match(text):
                raise ValidationError("時間格式須為 HH:MM", details={"value": text})
            if text not in times:
                times.append(text)
        if not times and frequency_code not in ("prn", None):
            raise ValidationError("請至少設定一個提醒時間")
        if len(times) > 8:
            raise ValidationError("一天最多 8 個提醒時間")
        return sorted(times)

    @staticmethod
    def _delivery_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "schedule_id": row["schedule_id"],
            "scheduled_for": _tw_iso(row["scheduled_for"]),
            "time_label": _tw_hhmm(row["scheduled_for"]),
            "status": row["status"],
            "responded_at": row.get("responded_at"),
        }

    @staticmethod
    def _status_label(schedule: dict[str, Any], today: date) -> str:
        if not schedule["active"]:
            return "stopped"
        paused = _parse_date(schedule.get("paused_until"))
        if paused and paused >= today:
            return "paused"
        end = _parse_date(schedule.get("end_date"))
        if end and end < today:
            return "finished"
        return "active"


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValidationError("日期格式須為 YYYY-MM-DD") from exc


def _to_tw(value: Any) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return as_utc(dt).astimezone(TAIPEI)


def _tw_hhmm(value: Any) -> str:
    return _to_tw(value).strftime("%H:%M")


def _tw_iso(value: Any) -> str:
    return _to_tw(value).isoformat()
