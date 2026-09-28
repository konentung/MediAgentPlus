"""服藥提醒 Repository：排程（medication_schedules）與推播紀錄（reminder_deliveries）。

所有 datetime 一律以 UTC 寫入；SQLite 不存時區，讀回來若是 naive 一律視為 UTC。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.medication import MedicationScheduleModel, ReminderDeliveryModel
from app.repositories.base import row_to_dict


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ScheduleRepository(Protocol):
    def get(self, schedule_id: str) -> dict[str, Any] | None: ...
    def list_for_user(self, user_id: str, *, active_only: bool = False) -> list[dict[str, Any]]: ...
    def list_due_on(self, day: date) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, schedule_id: str, **changes: Any) -> dict[str, Any]: ...
    def delete(self, schedule_id: str) -> None: ...


class DeliveryRepository(Protocol):
    def get(self, delivery_id: str) -> dict[str, Any] | None: ...
    def create_if_absent(
        self, schedule_id: str, scheduled_for: datetime
    ) -> dict[str, Any] | None: ...
    def update(self, delivery_id: str, **changes: Any) -> dict[str, Any]: ...
    def list_snoozed_due(self, now: datetime) -> list[dict[str, Any]]: ...
    def list_for_schedules(
        self, schedule_ids: list[str], *, since: datetime
    ) -> list[dict[str, Any]]: ...


class SqlAlchemyScheduleRepository:
    def get(self, schedule_id: str) -> dict[str, Any] | None:
        row = db.session.get(MedicationScheduleModel, schedule_id)
        return row_to_dict(row) if row else None

    def list_for_user(self, user_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        stmt = (
            db.select(MedicationScheduleModel)
            .where(MedicationScheduleModel.user_id == user_id)
            .order_by(MedicationScheduleModel.created_at.desc())
        )
        if active_only:
            stmt = stmt.where(MedicationScheduleModel.active.is_(True))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def list_due_on(self, day: date) -> list[dict[str, Any]]:
        """當天有效（啟用、在起訖區間內、未暫停）的排程。"""
        stmt = db.select(MedicationScheduleModel).where(
            MedicationScheduleModel.active.is_(True),
            MedicationScheduleModel.start_date <= day,
            db.or_(
                MedicationScheduleModel.end_date.is_(None),
                MedicationScheduleModel.end_date >= day,
            ),
            db.or_(
                MedicationScheduleModel.paused_until.is_(None),
                MedicationScheduleModel.paused_until < day,
            ),
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = MedicationScheduleModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, schedule_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(MedicationScheduleModel, schedule_id)
        if row is None:
            raise KeyError(schedule_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def delete(self, schedule_id: str) -> None:
        row = db.session.get(MedicationScheduleModel, schedule_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()


class SqlAlchemyDeliveryRepository:
    def get(self, delivery_id: str) -> dict[str, Any] | None:
        row = db.session.get(ReminderDeliveryModel, delivery_id)
        return row_to_dict(row) if row else None

    def create_if_absent(self, schedule_id: str, scheduled_for: datetime) -> dict[str, Any] | None:
        """同一排程同一時段只會有一筆；已存在（或同時被另一個程序建立）回 None。"""
        scheduled_for = as_utc(scheduled_for)
        exists = db.session.execute(
            db.select(ReminderDeliveryModel.id).where(
                ReminderDeliveryModel.schedule_id == schedule_id,
                ReminderDeliveryModel.scheduled_for == scheduled_for,
            )
        ).first()
        if exists:
            return None
        row = ReminderDeliveryModel(schedule_id=schedule_id, scheduled_for=scheduled_for)
        db.session.add(row)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return None
        return row_to_dict(row)

    def update(self, delivery_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(ReminderDeliveryModel, delivery_id)
        if row is None:
            raise KeyError(delivery_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def list_snoozed_due(self, now: datetime) -> list[dict[str, Any]]:
        stmt = db.select(ReminderDeliveryModel).where(
            ReminderDeliveryModel.status == "snoozed",
            ReminderDeliveryModel.snooze_until <= as_utc(now),
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def list_for_schedules(
        self, schedule_ids: list[str], *, since: datetime
    ) -> list[dict[str, Any]]:
        if not schedule_ids:
            return []
        stmt = (
            db.select(ReminderDeliveryModel)
            .where(
                ReminderDeliveryModel.schedule_id.in_(schedule_ids),
                ReminderDeliveryModel.scheduled_for >= as_utc(since),
            )
            .order_by(ReminderDeliveryModel.scheduled_for.desc())
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]
