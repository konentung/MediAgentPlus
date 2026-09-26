"""院所 Repository。"""

from __future__ import annotations

from typing import Any, Protocol

from app.extensions import db
from app.models.hospital import HospitalModel
from app.repositories.base import row_to_dict


class HospitalRepository(Protocol):
    def get(self, hospital_id: str) -> dict[str, Any] | None: ...
    def list(self, *, active_only: bool = True) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, hospital_id: str, **changes: Any) -> dict[str, Any]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...


class SqlAlchemyHospitalRepository:
    def get(self, hospital_id: str) -> dict[str, Any] | None:
        row = db.session.get(HospitalModel, hospital_id)
        return row_to_dict(row) if row else None

    def list(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        stmt = db.select(HospitalModel).order_by(HospitalModel.name)
        if active_only:
            stmt = stmt.where(HospitalModel.active.is_(True))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = HospitalModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, hospital_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(HospitalModel, hospital_id)
        if row is None:
            raise KeyError(hospital_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """批次新增或更新（以 id 為準），一次 commit。回傳 (新增數, 更新數)。"""
        ids = [r["id"] for r in rows]
        existing = {
            row.id: row
            for row in db.session.execute(
                db.select(HospitalModel).where(HospitalModel.id.in_(ids))
            ).scalars()
        }
        created = updated = 0
        for values in rows:
            row = existing.get(values["id"])
            if row is None:
                db.session.add(HospitalModel(**values))
                created += 1
            else:
                for key, value in values.items():
                    if key != "id" and value is not None:
                        setattr(row, key, value)
                updated += 1
        db.session.commit()
        return created, updated
