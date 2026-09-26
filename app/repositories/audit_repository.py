"""稽核紀錄 Repository。"""

from __future__ import annotations

from typing import Any, Protocol

from app.extensions import db
from app.models.audit import AuditLogModel
from app.repositories.base import row_to_dict


class AuditRepository(Protocol):
    def record(
        self,
        *,
        actor_id: str,
        action: str,
        target_user_id: str | None = None,
        hospital_id: str | None = None,
        ip_address: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def list(self, *, hospital_id: str | None, limit: int = 100) -> list[dict[str, Any]]: ...


class SqlAlchemyAuditRepository:
    def record(
        self,
        *,
        actor_id: str,
        action: str,
        target_user_id: str | None = None,
        hospital_id: str | None = None,
        ip_address: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = AuditLogModel(
            actor_id=actor_id,
            action=action,
            target_user_id=target_user_id,
            hospital_id=hospital_id,
            ip_address=ip_address,
            detail=detail,
        )
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def list(self, *, hospital_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = db.select(AuditLogModel).order_by(AuditLogModel.created_at.desc()).limit(limit)
        if hospital_id is not None:
            stmt = stmt.where(AuditLogModel.hospital_id == hospital_id)
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]
