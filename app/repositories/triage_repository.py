"""急診分流 Repository：快照（時序）、分流建議、訂閱。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.triage import ErSnapshotModel, TriageAdvisoryModel, TriageSubscriptionModel
from app.repositories.base import row_to_dict
from app.repositories.reminder_repository import as_utc


def _snapshot_dict(row: ErSnapshotModel) -> dict[str, Any]:
    data = row_to_dict(row, exclude=("created_at", "updated_at"))
    data["reported_at"] = as_utc(row.reported_at).isoformat()
    return data


class SnapshotRepository(Protocol):
    def add_missing(self, rows: list[dict[str, Any]]) -> int: ...
    def latest(self, *, area_code: str | None = None) -> list[dict[str, Any]]: ...
    def latest_reported_at(self) -> datetime | None: ...
    def average_wait_admit(self, *, since: datetime) -> dict[str, float]: ...
    def history(self, nhi_hospital_id: str, *, since: datetime) -> list[dict[str, Any]]: ...
    def purge_before(self, cutoff: datetime) -> int: ...
    def count(self) -> int: ...


class AdvisoryRepository(Protocol):
    def get(self, advisory_id: str) -> dict[str, Any] | None: ...
    def get_open(self, area_code: str) -> dict[str, Any] | None: ...
    def list(
        self, *, status: str | None = None, area_code: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, advisory_id: str, **changes: Any) -> dict[str, Any]: ...


class SubscriptionRepository(Protocol):
    def get(self, subscription_id: str) -> dict[str, Any] | None: ...
    def get_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None: ...
    def list(self, *, hospital_id: str | None = None) -> list[dict[str, Any]]: ...
    def list_active(self) -> list[dict[str, Any]]: ...
    def create(self, **values: Any) -> dict[str, Any]: ...
    def update(self, subscription_id: str, **changes: Any) -> dict[str, Any]: ...
    def delete(self, subscription_id: str) -> None: ...


class SqlAlchemySnapshotRepository:
    def add_missing(self, rows: list[dict[str, Any]]) -> int:
        """只新增 (醫院, 回報時間) 尚未存在的列；回傳新增筆數。"""
        if not rows:
            return 0
        times = {as_utc(r["reported_at"]) for r in rows}
        existing = {
            (hid, as_utc(at))
            for hid, at in db.session.execute(
                db.select(ErSnapshotModel.nhi_hospital_id, ErSnapshotModel.reported_at).where(
                    ErSnapshotModel.reported_at.in_(list(times))
                )
            )
        }
        added = 0
        for values in rows:
            key = (values["nhi_hospital_id"], as_utc(values["reported_at"]))
            if key in existing:
                continue
            db.session.add(ErSnapshotModel(**values))
            existing.add(key)
            added += 1
        db.session.commit()
        return added

    def latest(self, *, area_code: str | None = None) -> list[dict[str, Any]]:
        """每家醫院最新一筆。"""
        latest = (
            db.select(
                ErSnapshotModel.nhi_hospital_id.label("hid"),
                db.func.max(ErSnapshotModel.reported_at).label("at"),
            )
            .group_by(ErSnapshotModel.nhi_hospital_id)
            .subquery()
        )
        stmt = (
            db.select(ErSnapshotModel)
            .join(
                latest,
                db.and_(
                    ErSnapshotModel.nhi_hospital_id == latest.c.hid,
                    ErSnapshotModel.reported_at == latest.c.at,
                ),
            )
            .order_by(ErSnapshotModel.area_code, ErSnapshotModel.hospital_name)
        )
        if area_code:
            stmt = stmt.where(ErSnapshotModel.area_code == area_code)
        return [_snapshot_dict(r) for r in db.session.execute(stmt).scalars()]

    def latest_reported_at(self) -> datetime | None:
        value = db.session.execute(db.select(db.func.max(ErSnapshotModel.reported_at))).scalar()
        return as_utc(value) if value else None

    def average_wait_admit(self, *, since: datetime) -> dict[str, float]:
        rows = db.session.execute(
            db.select(ErSnapshotModel.nhi_hospital_id, db.func.avg(ErSnapshotModel.wait_admit))
            .where(ErSnapshotModel.reported_at >= as_utc(since))
            .group_by(ErSnapshotModel.nhi_hospital_id)
        )
        return {hid: float(avg) for hid, avg in rows if avg is not None}

    def history(self, nhi_hospital_id: str, *, since: datetime) -> list[dict[str, Any]]:
        stmt = (
            db.select(ErSnapshotModel)
            .where(
                ErSnapshotModel.nhi_hospital_id == nhi_hospital_id,
                ErSnapshotModel.reported_at >= as_utc(since),
            )
            .order_by(ErSnapshotModel.reported_at)
        )
        return [_snapshot_dict(r) for r in db.session.execute(stmt).scalars()]

    def purge_before(self, cutoff: datetime) -> int:
        result = db.session.execute(
            db.delete(ErSnapshotModel).where(ErSnapshotModel.reported_at < as_utc(cutoff))
        )
        db.session.commit()
        return int(result.rowcount or 0)

    def count(self) -> int:
        return db.session.execute(db.select(db.func.count(ErSnapshotModel.id))).scalar_one()


class SqlAlchemyAdvisoryRepository:
    def get(self, advisory_id: str) -> dict[str, Any] | None:
        row = db.session.get(TriageAdvisoryModel, advisory_id)
        return row_to_dict(row) if row else None

    def get_open(self, area_code: str) -> dict[str, Any] | None:
        row = db.session.execute(
            db.select(TriageAdvisoryModel)
            .where(TriageAdvisoryModel.area_code == area_code, TriageAdvisoryModel.status == "open")
            .order_by(TriageAdvisoryModel.opened_at.desc())
        ).scalar()
        return row_to_dict(row) if row else None

    def list(
        self, *, status: str | None = None, area_code: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        stmt = (
            db.select(TriageAdvisoryModel)
            .order_by(TriageAdvisoryModel.opened_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(TriageAdvisoryModel.status == status)
        if area_code:
            stmt = stmt.where(TriageAdvisoryModel.area_code == area_code)
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = TriageAdvisoryModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, advisory_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(TriageAdvisoryModel, advisory_id)
        if row is None:
            raise KeyError(advisory_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)


class SqlAlchemySubscriptionRepository:
    def get(self, subscription_id: str) -> dict[str, Any] | None:
        row = db.session.get(TriageSubscriptionModel, subscription_id)
        return row_to_dict(row) if row else None

    def get_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        row = db.session.execute(
            db.select(TriageSubscriptionModel).where(
                TriageSubscriptionModel.secret_fingerprint == fingerprint
            )
        ).scalar_one_or_none()
        return row_to_dict(row) if row else None

    def list(self, *, hospital_id: str | None = None) -> list[dict[str, Any]]:
        stmt = db.select(TriageSubscriptionModel).order_by(TriageSubscriptionModel.created_at)
        if hospital_id is not None:
            stmt = stmt.where(TriageSubscriptionModel.hospital_id == hospital_id)
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def list_active(self) -> list[dict[str, Any]]:
        stmt = db.select(TriageSubscriptionModel).where(TriageSubscriptionModel.active.is_(True))
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def create(self, **values: Any) -> dict[str, Any]:
        row = TriageSubscriptionModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def update(self, subscription_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(TriageSubscriptionModel, subscription_id)
        if row is None:
            raise KeyError(subscription_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def delete(self, subscription_id: str) -> None:
        row = db.session.get(TriageSubscriptionModel, subscription_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()
