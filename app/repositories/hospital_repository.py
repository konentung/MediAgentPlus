"""院所 Repository。"""

from __future__ import annotations

from typing import Any, Protocol

from app.extensions import db
from app.models.hospital import HospitalModel
from app.repositories.base import paginate, row_to_dict


class HospitalRepository(Protocol):
    def get(self, hospital_id: str) -> dict[str, Any] | None: ...
    def get_many(self, hospital_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    def list(
        self, *, active_only: bool = True, exclude_clinics: bool = False
    ) -> list[dict[str, Any]]: ...
    def paginate(
        self,
        *,
        query: str | None,
        region: str | None,
        tier: str | None,
        active_only: bool,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def search_nearby(
        self,
        *,
        city: str,
        district: str | None,
        tiers: tuple[str, ...],
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...
    def list_with_coords(self, *, tiers: tuple[str, ...] | None = None) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, hospital_id: str, **changes: Any) -> dict[str, Any]: ...
    def upsert_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]: ...


class SqlAlchemyHospitalRepository:
    def get(self, hospital_id: str) -> dict[str, Any] | None:
        row = db.session.get(HospitalModel, hospital_id)
        return row_to_dict(row) if row else None

    def get_many(self, hospital_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not hospital_ids:
            return {}
        rows = db.session.execute(
            db.select(HospitalModel).where(HospitalModel.id.in_(hospital_ids))
        ).scalars()
        return {row.id: row_to_dict(row) for row in rows}

    def list(
        self, *, active_only: bool = True, exclude_clinics: bool = False
    ) -> list[dict[str, Any]]:
        """下拉選單用；`exclude_clinics` 只留醫院層級（診所有上萬家，選單放不下）。"""
        stmt = db.select(HospitalModel).order_by(HospitalModel.name)
        if active_only:
            stmt = stmt.where(HospitalModel.active.is_(True))
        if exclude_clinics:
            tier = HospitalModel.capacity_tier
            stmt = stmt.where(db.or_(tier.is_(None), tier != "clinic"))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def paginate(
        self,
        *,
        query: str | None,
        region: str | None,
        tier: str | None,
        active_only: bool,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """後台「所有院所」表格：代碼 / 名稱 / 地址關鍵字、縣市、層級篩選。"""
        stmt = db.select(HospitalModel).order_by(HospitalModel.name, HospitalModel.id)
        if active_only:
            stmt = stmt.where(HospitalModel.active.is_(True))
        if query:
            contains = f"%{query}%"
            stmt = stmt.where(
                db.or_(
                    HospitalModel.id.ilike(contains),
                    HospitalModel.name.ilike(contains),
                    HospitalModel.address.ilike(contains),
                )
            )
        if region:
            variants = {region, region.replace("台", "臺"), region.replace("臺", "台")}
            stmt = stmt.where(HospitalModel.region.in_(list(variants)))
        if tier:
            stmt = stmt.where(HospitalModel.capacity_tier == tier)
        return paginate(stmt, page, per_page)

    def list_with_coords(self, *, tiers: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """所有有座標的啟用院所（依距離排序時用，交給呼叫端算距離）。"""
        stmt = db.select(HospitalModel).where(
            HospitalModel.active.is_(True),
            HospitalModel.lat.is_not(None),
            HospitalModel.lng.is_not(None),
        )
        if tiers:
            stmt = stmt.where(HospitalModel.capacity_tier.in_(list(tiers)))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def search_nearby(
        self,
        *,
        city: str,
        district: str | None,
        tiers: tuple[str, ...],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """依縣市（region 或地址開頭，臺 / 台皆可）與鄉鎮市區（地址包含）找啟用中的院所。"""
        variants = {city, city.replace("台", "臺"), city.replace("臺", "台")}
        stmt = (
            db.select(HospitalModel)
            .where(
                HospitalModel.active.is_(True),
                HospitalModel.capacity_tier.in_(list(tiers)),
                db.or_(
                    HospitalModel.region.in_(list(variants)),
                    *[HospitalModel.address.like(f"{v}%") for v in variants],
                ),
            )
            .order_by(HospitalModel.name)
            .limit(limit)
        )
        if district:
            stmt = stmt.where(HospitalModel.address.like(f"%{district}%"))
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
