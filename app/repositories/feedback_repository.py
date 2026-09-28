"""意見回饋 Repository，對應 `FeedbackModel`。"""

from __future__ import annotations

from typing import Any, Protocol

from app.extensions import db
from app.models.feedback import FeedbackModel
from app.repositories.base import row_to_dict


class FeedbackRepository(Protocol):
    def create(self, **fields: Any) -> dict[str, Any]: ...
    def get(self, feedback_id: str) -> dict[str, Any] | None: ...
    def update(self, feedback_id: str, **changes: Any) -> dict[str, Any]: ...
    def list(
        self, *, hospital_id: str | None, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]: ...
    def list_for_user(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def count_open(self, hospital_id: str | None) -> int: ...


class SqlAlchemyFeedbackRepository:
    def create(self, **fields: Any) -> dict[str, Any]:
        row = FeedbackModel(**fields)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def get(self, feedback_id: str) -> dict[str, Any] | None:
        row = db.session.get(FeedbackModel, feedback_id)
        return row_to_dict(row) if row else None

    def update(self, feedback_id: str, **changes: Any) -> dict[str, Any]:
        row = db.session.get(FeedbackModel, feedback_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return row_to_dict(row)

    def list(
        self, *, hospital_id: str | None, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        stmt = db.select(FeedbackModel).order_by(FeedbackModel.created_at.desc()).limit(limit)
        if hospital_id is not None:
            stmt = stmt.where(FeedbackModel.hospital_id == hospital_id)
        if status:
            stmt = stmt.where(FeedbackModel.status == status)
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def list_for_user(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        stmt = (
            db.select(FeedbackModel)
            .where(FeedbackModel.user_id == user_id)
            .order_by(FeedbackModel.created_at.desc())
            .limit(limit)
        )
        return [row_to_dict(r) for r in db.session.execute(stmt).scalars()]

    def count_open(self, hospital_id: str | None) -> int:
        stmt = db.select(db.func.count(FeedbackModel.id)).where(FeedbackModel.status == "open")
        if hospital_id is not None:
            stmt = stmt.where(FeedbackModel.hospital_id == hospital_id)
        return int(db.session.execute(stmt).scalar_one())
