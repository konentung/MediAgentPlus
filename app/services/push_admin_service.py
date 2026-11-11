"""後台「推播管理」：系統排程推播的總覽、紀錄與排程開關。

目前系統唯一的排程推播是服藥提醒（`medication_schedules` → `reminder_deliveries`，
由 `ReminderService.run_due()` 每 5 分鐘送出）；之後的每日任務推播也會放進這一頁。
事件通知（核對結果、院所確認、領藥備好…）是即時送出、沒有留紀錄，不在這裡。

這裡看的是**所有使用者**的資料（含藥名），所以權限 `PUSH_MANAGE` 目前只給 SUPER_ADMIN；
停用 / 啟用別人的排程會寫 `audit_logs`。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.core.pagination import PAGE_SIZE, page_payload
from app.domain.principal import Principal
from app.domain.reminder_rules import FREQUENCY_LABELS
from app.repositories.audit_repository import AuditRepository
from app.repositories.reminder_repository import (
    DeliveryRepository,
    ScheduleRepository,
    as_utc,
)
from app.repositories.user_repository import UserRepository
from app.services.reminder_service import ReminderService
from app.utils.clock import TAIPEI, now_tw

DELIVERY_STATUS_LABELS = {
    "pending": "待送出",
    "sent": "已送出",
    "taken": "已服用",
    "snoozed": "延後提醒",
    "skipped": "略過",
    "failed": "送出失敗",
}
SCHEDULE_STATUS_LABELS = {
    "active": "啟用中",
    "paused": "暫停中",
    "finished": "已結束",
    "stopped": "已停用",
}
AUDIT_DISABLE = "push.schedule_disable"
AUDIT_ENABLE = "push.schedule_enable"


class PushAdminService:
    def __init__(
        self,
        schedules: ScheduleRepository,
        deliveries: DeliveryRepository,
        users: UserRepository,
        audit: AuditRepository,
    ) -> None:
        self._schedules = schedules
        self._deliveries = deliveries
        self._users = users
        self._audit = audit

    def overview(self) -> dict[str, Any]:
        """今天（台灣時間）的推播數字，加上目前有效的排程數與收不到推播的人數。"""
        today = now_tw().date()
        counts = self._deliveries.count_by_status(since=datetime.combine(today, time.min, TAIPEI))
        return {
            "today": today.isoformat(),
            # 送出成功的 = 還沒回應（sent）+ 已經回應的（taken / snoozed）
            "sent_today": sum(counts.get(key, 0) for key in ("sent", "taken", "snoozed")),
            "taken_today": counts.get("taken", 0),
            "failed_today": counts.get("failed", 0),
            "skipped_today": counts.get("skipped", 0),
            "active_schedules": self._schedules.count_due_on(today),
            "push_disabled_users": self._users.count_push_disabled(),
        }

    def list_deliveries(self, *, status: str | None = None, page: int = 1) -> dict[str, Any]:
        if status and status not in DELIVERY_STATUS_LABELS:
            raise ValidationError(
                "status 不合法", details={"allowed": list(DELIVERY_STATUS_LABELS)}
            )
        rows, total = self._deliveries.paginate_all(status=status, page=page, per_page=PAGE_SIZE)
        items = []
        for row in rows:
            schedule = self._schedules.get(row["schedule_id"]) or {}
            items.append(
                {
                    "id": row["id"],
                    "scheduled_for": _tw_iso(row["scheduled_for"]),
                    "sent_at": _tw_iso(row.get("sent_at")),
                    "responded_at": _tw_iso(row.get("responded_at")),
                    "status": row["status"],
                    "status_label": DELIVERY_STATUS_LABELS.get(row["status"], row["status"]),
                    "drug_name": schedule.get("drug_name"),
                    **self._user_fields(schedule.get("user_id")),
                }
            )
        return page_payload(items, total, page)

    def list_schedules(self, *, active: bool | None = None, page: int = 1) -> dict[str, Any]:
        rows, total = self._schedules.paginate_all(active=active, page=page, per_page=PAGE_SIZE)
        return page_payload([self._schedule_public(row) for row in rows], total, page)

    def set_schedule_active(
        self, actor: Principal, schedule_id: str, active: Any
    ) -> dict[str, Any]:
        """停用 / 啟用某位使用者的提醒排程（例如使用者反映一直收到不該收的提醒）。

        啟用時一併清掉暫停日，跟使用者自己在 LIFF 按「恢復」一樣。
        """
        if not isinstance(active, bool):
            raise ValidationError("active 需為 true 或 false")
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise NotFoundError("找不到這個提醒排程")
        changes: dict[str, Any] = {"active": active}
        if active:
            changes["paused_until"] = None
        updated = self._schedules.update(schedule_id, **changes)
        self._audit.record(
            actor_id=actor.user_id or "",
            action=AUDIT_ENABLE if active else AUDIT_DISABLE,
            target_user_id=schedule["user_id"],
            detail={"schedule_id": schedule_id, "drug_name": schedule["drug_name"]},
        )
        return self._schedule_public(updated)

    # ---- helpers ----

    def _schedule_public(self, row: dict[str, Any]) -> dict[str, Any]:
        status = ReminderService._status_label(row, now_tw().date())
        return {
            "id": row["id"],
            "drug_name": row["drug_name"],
            "dose_text": row.get("dose_text"),
            "frequency_label": FREQUENCY_LABELS.get(row.get("frequency_code") or "other"),
            "times": row.get("times") or [],
            "start_date": row.get("start_date"),
            "end_date": row.get("end_date"),
            "paused_until": row.get("paused_until"),
            "active": row["active"],
            "status": status,
            "status_label": SCHEDULE_STATUS_LABELS[status],
            **self._user_fields(row.get("user_id")),
        }

    def _user_fields(self, user_id: str | None) -> dict[str, Any]:
        user = self._users.get_by_id(user_id) if user_id else None
        return {
            "user_id": user_id,
            "user_name": user.display_name if user else None,
            # 封鎖官方帳號的人：排程還在，但實際上送不出去
            "push_enabled": user.push_enabled if user else None,
        }


def _tw_iso(value: Any) -> str | None:
    """UTC 存的時間轉成台灣時間的 ISO 字串；SQLite 讀回來沒有時區的值視為 UTC。"""
    if not value:
        return None
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return as_utc(moment).astimezone(TAIPEI).isoformat()
