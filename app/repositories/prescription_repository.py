"""處方箋 Repository。"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from app.extensions import db
from app.models.medication import PrescriptionModel
from app.repositories.base import row_to_dict


class PrescriptionRepository(Protocol):
    def get(self, prescription_id: str) -> dict[str, Any] | None: ...
    def list_for_user(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, prescription_id: str, **changes: Any) -> dict[str, Any]: ...
    def list_refill_due(self, today: date) -> list[dict[str, Any]]: ...


class SqlAlchemyPrescriptionRepository:
    def get(self, prescription_id: str) -> dict[str, Any] | None:
        row = db.session.get(PrescriptionModel, prescription_id)
        return row_to_dict(row) if row else None

    def list_for_user(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        stmt = (
            db.select(PrescriptionModel)
            .where(PrescriptionModel.user_id == user_id, PrescriptionModel.status != "discarded")
            .order_by(PrescriptionModel.created_at.desc())
            .limit(limit)
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = PrescriptionModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, prescription_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(PrescriptionModel, prescription_id)
        if row is None:
            raise KeyError(prescription_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def list_refill_due(self, today: date) -> list[dict[str, Any]]:
        """慢箋領藥提醒：提醒日已到、尚未提醒過、已確認的處方。"""
        stmt = db.select(PrescriptionModel).where(
            PrescriptionModel.status == "confirmed",
            PrescriptionModel.refill_reminder_on.is_not(None),
            PrescriptionModel.refill_reminder_on <= today,
            PrescriptionModel.refill_reminded_at.is_(None),
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]
