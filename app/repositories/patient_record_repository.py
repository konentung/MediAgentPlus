"""院方病人紀錄 Repository：patient_records 及其底下的審核、就診、風險、追蹤、備註。

以 dict 與 Service 層溝通（`row_to_dict`），子表 CRUD 走同一組泛型方法避免重複。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.patient_record import (
    PatientEncounterModel,
    PatientFollowUpModel,
    PatientRecordModel,
    PatientRecordNoteModel,
    PatientRecordReviewModel,
    PatientRiskFlagModel,
)
from app.repositories.base import row_to_dict

CHILD_MODELS: dict[str, type[db.Model]] = {
    "reviews": PatientRecordReviewModel,
    "encounters": PatientEncounterModel,
    "flags": PatientRiskFlagModel,
    "follow_ups": PatientFollowUpModel,
    "notes": PatientRecordNoteModel,
}
_DATE_COLUMNS = {"visit_date", "admitted_at", "discharged_at", "due_date"}


class PatientRecordRepository(Protocol):
    def get(self, user_id: str, hospital_id: str) -> dict[str, Any] | None: ...
    def get_by_id(self, record_id: str) -> dict[str, Any] | None: ...
    def list_for_user(self, user_id: str) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, record_id: str, **changes: Any) -> dict[str, Any]: ...
    def add_child(self, collection: str, record_id: str, **values: Any) -> dict[str, Any]: ...
    def update_child(
        self, collection: str, record_id: str, child_id: str, **changes: Any
    ) -> dict[str, Any] | None: ...
    def list_children(self, collection: str, record_id: str) -> list[dict[str, Any]]: ...
    def latest_discharge_code(self, user_id: str) -> str | None: ...
    def count_due_follow_ups(self, hospital_id: str | None, on: date) -> int: ...


def _coerce(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for key in _DATE_COLUMNS & out.keys():
        if isinstance(out[key], str) and out[key]:
            out[key] = date.fromisoformat(out[key])
    return out


class SqlAlchemyPatientRecordRepository:
    def get(self, user_id: str, hospital_id: str) -> dict[str, Any] | None:
        row = db.session.execute(
            db.select(PatientRecordModel).where(
                PatientRecordModel.user_id == user_id,
                PatientRecordModel.hospital_id == hospital_id,
            )
        ).scalar_one_or_none()
        return row_to_dict(row) if row else None

    def get_by_id(self, record_id: str) -> dict[str, Any] | None:
        row = db.session.get(PatientRecordModel, record_id)
        return row_to_dict(row) if row else None

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = db.session.execute(
            db.select(PatientRecordModel)
            .where(PatientRecordModel.user_id == user_id)
            .order_by(PatientRecordModel.created_at)
        ).scalars()
        return [row_to_dict(r) for r in rows]

    def create(self, **values: Any) -> dict[str, Any]:
        row = PatientRecordModel(identity_verified_at=datetime.now(UTC), **values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, record_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(PatientRecordModel, record_id)
        if row is None:
            raise KeyError(record_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def add_child(self, collection: str, record_id: str, **values: Any) -> dict[str, Any]:
        model = CHILD_MODELS[collection]
        row = model(record_id=record_id, **_coerce(values))
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update_child(
        self, collection: str, record_id: str, child_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        model = CHILD_MODELS[collection]
        row = db.session.get(model, child_id)
        # 檢查 record_id 是為了避免跨病人 / 跨院所改到別人的資料
        if row is None or row.record_id != record_id:
            return None
        for key, value in _coerce(changes).items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def list_children(self, collection: str, record_id: str) -> list[dict[str, Any]]:
        model = CHILD_MODELS[collection]
        rows = db.session.execute(
            db.select(model).where(model.record_id == record_id).order_by(model.created_at.desc())
        ).scalars()
        return [row_to_dict(r) for r in rows]

    def latest_discharge_code(self, user_id: str) -> str | None:
        """最近一次出院代碼（衛教個人化 Roadmap 第 3 項用）。"""
        return db.session.execute(
            db.select(PatientEncounterModel.discharge_code)
            .join(PatientRecordModel, PatientRecordModel.id == PatientEncounterModel.record_id)
            .where(
                PatientRecordModel.user_id == user_id,
                PatientEncounterModel.discharge_code.is_not(None),
            )
            .order_by(PatientEncounterModel.discharged_at.desc().nulls_last())
            .limit(1)
        ).scalar_one_or_none()

    def count_due_follow_ups(self, hospital_id: str | None, on: date) -> int:
        stmt = (
            db.select(db.func.count(PatientFollowUpModel.id))
            .join(PatientRecordModel, PatientRecordModel.id == PatientFollowUpModel.record_id)
            .where(PatientFollowUpModel.status == "open", PatientFollowUpModel.due_date <= on)
        )
        if hospital_id is not None:
            stmt = stmt.where(PatientRecordModel.hospital_id == hospital_id)
        return int(db.session.execute(stmt).scalar_one())
