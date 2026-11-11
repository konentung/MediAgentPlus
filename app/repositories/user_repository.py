"""使用者 Repository：`UserRecord` 是 Service 層用的資料結構，實作對應 `UserModel`。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.domain.enums import END_USER_ROLES, HOSPITAL_ROLES, BindingStatus, Role
from app.extensions import db
from app.models.user import UserModel


@dataclass
class UserRecord:
    id: str
    line_user_id: str | None
    display_name: str | None = None
    picture_url: str | None = None
    role: Role = Role.GUEST
    binding_status: BindingStatus = BindingStatus.UNBOUND
    hospital_id: str | None = None
    pharmacy_id: str | None = None
    push_enabled: bool = True
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
        pharmacy_id: str | None = None,
        include_unassigned: bool = False,
        query: str | None = None,
        limit: int = 100,
    ) -> list[UserRecord]: ...
    def pharmacy_ids_with_staff(self, pharmacy_ids: list[str]) -> set[str]: ...
    def count_push_disabled(self) -> int: ...


def _to_record(row: UserModel) -> UserRecord:
    return UserRecord(
        id=row.id,
        line_user_id=row.line_user_id,
        display_name=row.display_name,
        picture_url=row.picture_url,
        role=row.role,
        binding_status=row.binding_status,
        hospital_id=row.hospital_id,
        pharmacy_id=row.pharmacy_id,
        push_enabled=row.push_enabled,
        created_at=row.created_at,
    )


def _apply(row: UserModel, user: UserRecord) -> None:
    row.line_user_id = user.line_user_id
    row.display_name = user.display_name
    row.picture_url = user.picture_url
    row.role = user.role
    row.binding_status = user.binding_status
    row.hospital_id = user.hospital_id
    row.pharmacy_id = user.pharmacy_id
    row.push_enabled = user.push_enabled


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
        pharmacy_id: str | None = None,
        include_unassigned: bool = False,
        query: str | None = None,
        limit: int = 100,
    ) -> list[UserRecord]:
        """後台人員管理用：依角色 / 所屬單位（院所或藥局）/ 顯示名稱（或 id）搜尋。

        `include_unassigned=True` 時，單位範圍內另外包含「尚未指派任何單位的一般使用者」，
        讓單位管理員能為剛登入的新同仁指派角色。
        """
        stmt = db.select(UserModel).order_by(UserModel.created_at.desc()).limit(limit)
        if roles is not None:
            stmt = stmt.where(UserModel.role.in_(list(roles)))
        if hospital_id is not None or pharmacy_id is not None:
            scope = (
                UserModel.hospital_id == hospital_id
                if hospital_id is not None
                else UserModel.pharmacy_id == pharmacy_id
            )
            if include_unassigned:
                scope = db.or_(
                    scope,
                    db.and_(
                        UserModel.hospital_id.is_(None),
                        UserModel.pharmacy_id.is_(None),
                        UserModel.role.in_(list(END_USER_ROLES)),
                    ),
                )
            stmt = stmt.where(scope)
        if query:
            stmt = stmt.where(
                db.or_(UserModel.display_name.ilike(f"%{query}%"), UserModel.id == query)
            )
        return [_to_record(row) for row in db.session.execute(stmt).scalars()]

    def pharmacy_ids_with_staff(self, pharmacy_ids: list[str]) -> set[str]:
        """這些藥局裡，哪幾家有人員帳號（= 有人會在後台處理領藥需求）。"""
        if not pharmacy_ids:
            return set()
        stmt = (
            db.select(UserModel.pharmacy_id)
            .where(
                UserModel.pharmacy_id.in_(pharmacy_ids), UserModel.role.in_(list(HOSPITAL_ROLES))
            )
            .distinct()
        )
        return set(db.session.execute(stmt).scalars())

    def count_push_disabled(self) -> int:
        """封鎖官方帳號、收不到任何推播的使用者數。"""
        stmt = db.select(db.func.count(UserModel.id)).where(UserModel.push_enabled.is_(False))
        return int(db.session.execute(stmt).scalar_one())
