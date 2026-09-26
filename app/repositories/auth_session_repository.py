"""登入工作階段 Repository。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.extensions import db
from app.models.auth_session import AuthSessionModel
from app.repositories.base import row_to_dict


class AuthSessionRepository(Protocol):
    def create(self, **values: Any) -> dict[str, Any]: ...
    def get(self, session_id: str) -> dict[str, Any] | None: ...
    def mark_rotated(self, session_id: str, replaced_by: str) -> None: ...
    def revoke_family(self, family_id: str) -> int: ...
    def revoke_all_for_user(self, user_id: str) -> int: ...
    def purge_expired(self) -> int: ...


class SqlAlchemyAuthSessionRepository:
    def create(self, **values: Any) -> dict[str, Any]:
        row = AuthSessionModel(**values)
        db.session.add(row)
        db.session.commit()
        return row_to_dict(row)

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = db.session.get(AuthSessionModel, session_id)
        return row_to_dict(row) if row else None

    def mark_rotated(self, session_id: str, replaced_by: str) -> None:
        row = db.session.get(AuthSessionModel, session_id)
        if row is not None:
            row.used_at = datetime.now(UTC)
            row.replaced_by = replaced_by
            db.session.commit()

    def revoke_family(self, family_id: str) -> int:
        return self._revoke(AuthSessionModel.family_id == family_id)

    def revoke_all_for_user(self, user_id: str) -> int:
        return self._revoke(AuthSessionModel.user_id == user_id)

    def _revoke(self, condition: Any) -> int:
        result = db.session.execute(
            db.update(AuthSessionModel)
            .where(condition, AuthSessionModel.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        db.session.commit()
        return int(result.rowcount or 0)

    def purge_expired(self) -> int:
        result = db.session.execute(
            db.delete(AuthSessionModel).where(AuthSessionModel.expires_at < datetime.now(UTC))
        )
        db.session.commit()
        return int(result.rowcount or 0)
