"""領藥需求 Repository：病人把處方指定到某家藥局（`prescription_pickups`）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.medication import PrescriptionPickupModel
from app.repositories.base import paginate, row_to_dict


class PickupRepository(Protocol):
    def get(self, pickup_id: str) -> dict[str, Any] | None: ...
    def get_for_prescription(self, prescription_id: str) -> dict[str, Any] | None: ...
    def request(
        self, *, prescription_id: str, user_id: str, pharmacy_id: str
    ) -> dict[str, Any]: ...
    def update(self, pickup_id: str, **changes: Any) -> dict[str, Any]: ...
    def paginate(
        self, *, pharmacy_id: str | None, status: str | None, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]: ...
    def count(self, *, pharmacy_id: str | None, status: str) -> int: ...


class SqlAlchemyPickupRepository:
    def get(self, pickup_id: str) -> dict[str, Any] | None:
        row = db.session.get(PrescriptionPickupModel, pickup_id)
        return row_to_dict(row) if row else None

    def get_for_prescription(self, prescription_id: str) -> dict[str, Any] | None:
        row = self._by_prescription(prescription_id)
        return row_to_dict(row) if row else None

    def request(self, *, prescription_id: str, user_id: str, pharmacy_id: str) -> dict[str, Any]:
        """一張處方只會有一筆：已有就改指到新的藥局並回到 requested（清掉上一家的處理紀錄）。"""
        row = self._by_prescription(prescription_id)
        if row is None:
            row = PrescriptionPickupModel(prescription_id=prescription_id, user_id=user_id)
            db.session.add(row)
        row.pharmacy_id = pharmacy_id
        row.status = "requested"
        row.note = None
        row.handled_by = None
        row.requested_at = datetime.now(UTC)
        row.ready_at = None
        row.picked_up_at = None
        db.session.commit()
        return row_to_dict(row)

    def update(self, pickup_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(PrescriptionPickupModel, pickup_id)
        if row is None:
            raise KeyError(pickup_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def paginate(
        self, *, pharmacy_id: str | None, status: str | None, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]:
        """`pharmacy_id=None` = 不限藥局（只有 SUPER_ADMIN 會這樣查）；新的需求排前面。"""
        stmt = db.select(PrescriptionPickupModel).order_by(
            PrescriptionPickupModel.requested_at.desc(), PrescriptionPickupModel.id
        )
        if pharmacy_id is not None:
            stmt = stmt.where(PrescriptionPickupModel.pharmacy_id == pharmacy_id)
        if status:
            stmt = stmt.where(PrescriptionPickupModel.status == status)
        return paginate(stmt, page, per_page)

    def count(self, *, pharmacy_id: str | None, status: str) -> int:
        stmt = db.select(db.func.count(PrescriptionPickupModel.id)).where(
            PrescriptionPickupModel.status == status
        )
        if pharmacy_id is not None:
            stmt = stmt.where(PrescriptionPickupModel.pharmacy_id == pharmacy_id)
        return int(db.session.execute(stmt).scalar_one())

    @staticmethod
    def _by_prescription(prescription_id: str) -> PrescriptionPickupModel | None:
        return db.session.execute(
            db.select(PrescriptionPickupModel).where(
                PrescriptionPickupModel.prescription_id == prescription_id
            )
        ).scalar_one_or_none()
