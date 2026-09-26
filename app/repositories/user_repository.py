"""使用者 Repository。

`UserRecord` 是 Service 層使用的資料結構；`UserRepository` Protocol 定義了介面，
`InMemoryUserRepository` 與 `SqlAlchemyUserRepository`（對應 `app.models.user.UserModel`）
皆實作同一介面，替換時 Service / API 層不需改動。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from app.domain.enums import END_USER_ROLES, BindingStatus, Role
from app.extensions import db
from app.models.user import UserModel
from app.repositories.base import InMemoryStore


@dataclass
class UserRecord:
    id: str
    line_user_id: str | None
    display_name: str | None = None
    picture_url: str | None = None
    role: Role = Role.GUEST
    binding_status: BindingStatus = BindingStatus.UNBOUND
    hospital_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


class UserRepository(Protocol):
    def get_by_id(self, user_id: str) -> UserRecord | None: ...
    def get_by_line_user_id(self, line_user_id: str) -> UserRecord | None: ...
    def save(self, user: UserRecord) -> UserRecord: ...
    def update(self, user_id: str, **changes) -> UserRecord: ...
    def search(
        self,
        *,
        roles: frozenset[Role] | None = None,
        hospital_id: str | None = None,
        include_unassigned: bool = False,
        query: str | None = None,
        limit: int = 100,
    ) -> list[UserRecord]: ...


class InMemoryUserRepository:
    def __init__(self) -> None:
        self._store: InMemoryStore[UserRecord] = InMemoryStore(lambda u: u.id)

    def get_by_id(self, user_id: str) -> UserRecord | None:
        return self._store.get(user_id)

    def get_by_line_user_id(self, line_user_id: str) -> UserRecord | None:
        return self._store.find_one(lambda u: u.line_user_id == line_user_id)

    def save(self, user: UserRecord) -> UserRecord:
        return self._store.save(user)

    def update(self, user_id: str, **changes) -> UserRecord:
        current = self._store.get(user_id)
        if current is None:
            raise KeyError(user_id)
        return self._store.save(replace(current, **changes))

    def search(
        self,
        *,
        roles: frozenset[Role] | None = None,
        hospital_id: str | None = None,
        include_unassigned: bool = False,
        query: str | None = None,
        limit: int = 100,
    ) -> list[UserRecord]:
        rows = [
            u
            for u in self._store
            if (roles is None or u.role in roles)
            and (
                hospital_id is None
                or u.hospital_id == hospital_id
                or (include_unassigned and u.hospital_id is None and u.role.is_end_user)
            )
            and (not query or query.lower() in (u.display_name or "").lower() or query == u.id)
        ]
        return rows[:limit]


def _to_record(row: UserModel) -> UserRecord:
    return UserRecord(
        id=row.id,
        line_user_id=row.line_user_id,
        display_name=row.display_name,
        picture_url=row.picture_url,
        role=row.role,
        binding_status=row.binding_status,
        hospital_id=row.hospital_id,
        created_at=row.created_at,
    )


def _apply(row: UserModel, user: UserRecord) -> None:
    row.line_user_id = user.line_user_id
    row.display_name = user.display_name
    row.picture_url = user.picture_url
    row.role = user.role
    row.binding_status = user.binding_status
    row.hospital_id = user.hospital_id


class SqlAlchemyUserRepository:
    def get_by_id(self, user_id: str) -> UserRecord | None:
        row = db.session.get(UserModel, user_id)
        return _to_record(row) if row else None

    def get_by_line_user_id(self, line_user_id: str) -> UserRecord | None:
        row = db.session.execute(
            db.select(UserModel).where(UserModel.line_user_id == line_user_id)
        ).scalar_one_or_none()
        return _to_record(row) if row else None

    def save(self, user: UserRecord) -> UserRecord:
        row = db.session.get(UserModel, user.id)
        if row is None:
            row = UserModel(id=user.id, created_at=user.created_at)
            db.session.add(row)
        _apply(row, user)
        db.session.commit()
        return _to_record(row)

    def update(self, user_id: str, **changes) -> UserRecord:
        row = db.session.get(UserModel, user_id)
        if row is None:
            raise KeyError(user_id)
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()
        return _to_record(row)

    def search(
        self,
        *,
        roles: frozenset[Role] | None = None,
        hospital_id: str | None = None,
        include_unassigned: bool = False,
        query: str | None = None,
        limit: int = 100,
    ) -> list[UserRecord]:
        """後台人員管理用：依角色 / 院所 / 顯示名稱（或 id）搜尋。

        `include_unassigned=True` 時，院所範圍內另外包含「尚未指派院所的一般使用者」，
        讓院所管理員能為剛登入的新同仁指派角色。
        """
        stmt = db.select(UserModel).order_by(UserModel.created_at.desc()).limit(limit)
        if roles is not None:
            stmt = stmt.where(UserModel.role.in_(list(roles)))
        if hospital_id is not None:
            scope = UserModel.hospital_id == hospital_id
            if include_unassigned:
                scope = db.or_(
                    scope,
                    db.and_(
                        UserModel.hospital_id.is_(None), UserModel.role.in_(list(END_USER_ROLES))
                    ),
                )
            stmt = stmt.where(scope)
        if query:
            stmt = stmt.where(
                db.or_(UserModel.display_name.ilike(f"%{query}%"), UserModel.id == query)
            )
        return [_to_record(row) for row in db.session.execute(stmt).scalars()]
