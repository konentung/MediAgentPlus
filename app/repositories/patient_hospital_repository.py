"""病人的就醫院所（patient_hospitals）：每家院所一筆，病人自己加或院方加入本院。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.hospital import HospitalModel
from app.models.patient import PatientHospitalModel
from app.repositories.base import row_to_dict

SOURCE_PATIENT = "patient"  # 病人在 LIFF「我的就醫院所」自己加入，當下即視為確認
SOURCE_HOSPITAL = "hospital"  # 院方在後台加入本院，要病人在 LINE 上按「是」才算確認


class PatientHospitalRepository(Protocol):
    def get(self, user_id: str, hospital_id: str) -> dict[str, Any] | None: ...
    def list_for_user(self, user_id: str) -> list[dict[str, Any]]: ...
    def list_for_users(self, user_ids: list[str]) -> dict[str, list[dict[str, Any]]]: ...
    def upsert(
        self, user_id: str, hospital_id: str, *, source: str, confirmed: bool
    ) -> dict[str, Any]: ...
    def delete(self, user_id: str, hospital_id: str) -> None: ...


def _public(row: PatientHospitalModel, hospital_name: str | None) -> dict[str, Any]:
    data = row_to_dict(row)
    data["hospital_name"] = hospital_name
    data["confirmed"] = row.confirmed_at is not None
    return data


class SqlAlchemyPatientHospitalRepository:
    def get(self, user_id: str, hospital_id: str) -> dict[str, Any] | None:
        row = self._row(user_id, hospital_id)
        if row is None:
            return None
        hospital = db.session.get(HospitalModel, hospital_id)
        return _public(row, hospital.name if hospital else None)

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return self.list_for_users([user_id]).get(user_id, [])

    def list_for_users(self, user_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """一次取多位病人的就醫院所（含名稱），院所清單頁用；加入順序由舊到新。"""
        if not user_ids:
            return {}
        stmt = (
            db.select(PatientHospitalModel, HospitalModel.name)
            .outerjoin(HospitalModel, HospitalModel.id == PatientHospitalModel.hospital_id)
            .where(PatientHospitalModel.user_id.in_(user_ids))
            .order_by(PatientHospitalModel.requested_at.asc(), PatientHospitalModel.id)
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for row, name in db.session.execute(stmt):
            result.setdefault(row.user_id, []).append(_public(row, name))
        return result

    def upsert(
        self, user_id: str, hospital_id: str, *, source: str, confirmed: bool
    ) -> dict[str, Any]:
        """新增一筆；已有同院所的那筆就只補確認時間（不會把已確認的改回未確認）。"""
        now = datetime.now(UTC)
        row = self._row(user_id, hospital_id)
        if row is None:
            row = PatientHospitalModel(
                user_id=user_id,
                hospital_id=hospital_id,
                source=source,
                requested_at=now,
                confirmed_at=now if confirmed else None,
            )
            db.session.add(row)
        elif confirmed and row.confirmed_at is None:
            row.confirmed_at = now
        db.session.commit()
        hospital = db.session.get(HospitalModel, hospital_id)
        return _public(row, hospital.name if hospital else None)

    def delete(self, user_id: str, hospital_id: str) -> None:
        row = self._row(user_id, hospital_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()

    @staticmethod
    def _row(user_id: str, hospital_id: str) -> PatientHospitalModel | None:
        return db.session.execute(
            db.select(PatientHospitalModel).where(
                PatientHospitalModel.user_id == user_id,
                PatientHospitalModel.hospital_id == hospital_id,
            )
        ).scalar_one_or_none()
