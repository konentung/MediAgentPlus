"""使用者 Repository。

`UserRecord` 是資料庫模型設計前的暫時資料結構；`UserRepository` Protocol 定義了
Service 層需要的介面，之後只要另外實作 `SqlAlchemyUserRepository` 即可無痛替換。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from app.domain.enums import BindingStatus, IdentityType, Role
from app.repositories.base import InMemoryStore


@dataclass
class UserRecord:
    id: str
    line_user_id: str | None
    display_name: str | None = None
    picture_url: str | None = None
    role: Role = Role.GUEST
    binding_status: BindingStatus = BindingStatus.UNBOUND
    identity_type: IdentityType | None = None
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
