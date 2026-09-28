"""藥局與藥品 Repository（唯讀參考資料，只有 CLI 匯入會寫入）。"""

from __future__ import annotations

from typing import Any, Protocol

from app.extensions import db
from app.models.medication import DrugModel, PharmacyModel
from app.repositories.base import row_to_dict


class PharmacyRepository(Protocol):
    def get(self, pharmacy_id: str) -> dict[str, Any] | None: ...
    def search(
        self, *, city: str | None, district: str | None, nhi_only: bool = True, limit: int = 20
    ) -> list[dict[str, Any]]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...
    def count(self) -> int: ...


class DrugRepository(Protocol):
    def get(self, nhi_code: str) -> dict[str, Any] | None: ...
    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...
    def count(self) -> int: ...


def _upsert(model: type, key: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """以主鍵批次新增或更新，一次 commit。回傳 (新增數, 更新數)。"""
    keys = [r[key] for r in rows]
    column = getattr(model, key)
    existing = {
        getattr(row, key): row
        for row in db.session.execute(db.select(model).where(column.in_(keys))).scalars()
    }
    created = updated = 0
    for values in rows:
        row = existing.get(values[key])
        if row is None:
            row = model(**values)
            db.session.add(row)
            existing[values[key]] = row
            created += 1
        else:
            for field, value in values.items():
                if field != key:
                    setattr(row, field, value)
            updated += 1
    db.session.commit()
    return created, updated


class SqlAlchemyPharmacyRepository:
    def get(self, pharmacy_id: str) -> dict[str, Any] | None:
        row = db.session.get(PharmacyModel, pharmacy_id)
        return row_to_dict(row) if row else None

    def search(
        self, *, city: str | None, district: str | None, nhi_only: bool = True, limit: int = 20
    ) -> list[dict[str, Any]]:
        stmt = (
            db.select(PharmacyModel)
            .where(PharmacyModel.active.is_(True))
            .order_by(PharmacyModel.name)
            .limit(limit)
        )
        if city:
            stmt = stmt.where(PharmacyModel.city == city)
        if district:
            stmt = stmt.where(PharmacyModel.district == district)
        if nhi_only:
            stmt = stmt.where(PharmacyModel.nhi_contracted.is_(True))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        return _upsert(PharmacyModel, "id", rows)

    def count(self) -> int:
        return db.session.execute(db.select(db.func.count(PharmacyModel.id))).scalar_one()


class SqlAlchemyDrugRepository:
    def get(self, nhi_code: str) -> dict[str, Any] | None:
        row = db.session.get(DrugModel, nhi_code)
        return row_to_dict(row) if row else None

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """先找「名稱開頭相符」，再找「包含」；英文不分大小寫。"""
        text = query.strip()
        if not text:
            return []
        prefix = f"{text}%"
        contains = f"%{text}%"
        stmt = (
            db.select(DrugModel)
            .where(
                db.or_(
                    DrugModel.name_en.ilike(contains),
                    DrugModel.name_zh.ilike(contains),
                    DrugModel.ingredient.ilike(contains),
                )
            )
            .order_by(
                db.case(
                    (DrugModel.name_en.ilike(prefix), 0),
                    (DrugModel.name_zh.ilike(prefix), 1),
                    else_=2,
                ),
                DrugModel.name_en,
            )
            .limit(limit)
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        return _upsert(DrugModel, "nhi_code", rows)

    def count(self) -> int:
        return db.session.execute(db.select(db.func.count(DrugModel.nhi_code))).scalar_one()
