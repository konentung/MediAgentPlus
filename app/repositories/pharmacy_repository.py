"""藥局與藥品 Repository（開放資料匯入的參考資料；藥局可由自己的人員在後台修正基本資料）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.medication import DrugModel, PharmacyModel
from app.repositories.base import paginate, row_to_dict


class PharmacyRepository(Protocol):
    def get(self, pharmacy_id: str) -> dict[str, Any] | None: ...
    def search(
        self, *, city: str | None, district: str | None, nhi_only: bool = True, limit: int = 20
    ) -> list[dict[str, Any]]: ...
    def paginate(
        self,
        *,
        city: str | None,
        district: str | None,
        query: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...
    def update(self, pharmacy_id: str, **changes: Any) -> dict[str, Any]: ...
    def count(self) -> int: ...


class DrugRepository(Protocol):
    def get(self, nhi_code: str) -> dict[str, Any] | None: ...
    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...
    def find_mentioned(self, text: str, *, limit: int = 5) -> list[dict[str, Any]]: ...
    def paginate(
        self, *, query: str | None, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...
    def count(self) -> int: ...


# 藥局自己在後台改過的欄位：之後重新匯入開放資料時不覆蓋（見 PharmacyModel.self_managed_at）
SELF_MANAGED_FIELDS = frozenset({"phone", "address", "city", "district", "opening_hours"})


def _upsert(
    model: type, key: str, rows: list[dict[str, Any]], *, keep: frozenset[str] = frozenset()
) -> tuple[int, int]:
    """以主鍵批次新增或更新，一次 commit。回傳 (新增數, 更新數)。

    `keep`：既有列的 `self_managed_at` 有值時不覆蓋的欄位（只有藥局用得到）。
    """
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
            locked = keep if keep and getattr(row, "self_managed_at", None) else frozenset()
            for field, value in values.items():
                if field != key and field not in locked:
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

    def paginate(
        self,
        *,
        city: str | None,
        district: str | None,
        query: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int]:
        stmt = db.select(PharmacyModel).order_by(
            PharmacyModel.city, PharmacyModel.district, PharmacyModel.name, PharmacyModel.id
        )
        if city:
            stmt = stmt.where(PharmacyModel.city == city)
        if district:
            stmt = stmt.where(PharmacyModel.district == district)
        if query:
            contains = f"%{query}%"
            stmt = stmt.where(
                db.or_(
                    PharmacyModel.id.ilike(contains),
                    PharmacyModel.name.ilike(contains),
                    PharmacyModel.address.ilike(contains),
                )
            )
        return paginate(stmt, page, per_page)

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        return _upsert(PharmacyModel, "id", rows, keep=SELF_MANAGED_FIELDS)

    def update(self, pharmacy_id: str, **changes: Any) -> dict[str, Any]:
        """後台修改藥局資料；記下 `self_managed_at`，之後的開放資料匯入不會蓋掉這些欄位。"""
        row = db.session.get(PharmacyModel, pharmacy_id)
        if row is None:
            raise KeyError(pharmacy_id)
        for key, value in changes.items():
            setattr(row, key, value)
        row.self_managed_at = datetime.now(UTC)
        db.session.commit()
        return row_to_dict(row)

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

    def find_mentioned(self, text: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """自由文字裡「包含」哪些藥品的中文名（jieba 不認得商品名，改讓資料庫比對）。

        整個中文名出現在文字裡，或名稱前 3 字出現（「普拿疼」→「普拿疼伏冒錠」，
        中文名常帶劑量尾巴）；名稱長的優先。
        """
        name = DrugModel.name_zh
        head = db.func.substr(name, 1, 3)
        stmt = (
            db.select(DrugModel)
            .where(
                name.isnot(None),
                db.func.length(name) >= 2,
                db.or_(
                    db.literal(text).contains(name),
                    db.and_(db.func.length(name) >= 3, db.literal(text).contains(head)),
                ),
            )
            .order_by(db.func.length(name).desc(), DrugModel.name_en)
            .limit(limit)
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def paginate(
        self, *, query: str | None, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]:
        stmt = db.select(DrugModel).order_by(DrugModel.name_en, DrugModel.nhi_code)
        if query:
            contains = f"%{query}%"
            stmt = stmt.where(
                db.or_(
                    DrugModel.nhi_code.ilike(contains),
                    DrugModel.name_en.ilike(contains),
                    DrugModel.name_zh.ilike(contains),
                    DrugModel.ingredient.ilike(contains),
                )
            )
        return paginate(stmt, page, per_page)

    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        return _upsert(DrugModel, "nhi_code", rows)

    def count(self) -> int:
        return db.session.execute(db.select(db.func.count(DrugModel.nhi_code))).scalar_one()
