"""綁定流程（B1–B9）所需的 Repository：綁定工作階段、院方識別名單、客服工單。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from app.domain.enums import IdentityType, VerificationMethod
from app.repositories.base import InMemoryStore


class BindingStep(StrEnum):
    IDENTITY = "identity"  # B2
    PROFILE = "profile"  # B3
    VERIFICATION = "verification"  # B4
    CONSENT = "consent"  # B5
    MATCHING = "matching"  # 與院方識別名單比對
    DONE = "done"  # B7


@dataclass
class BindingSession:
    user_id: str
    step: BindingStep = BindingStep.IDENTITY
    identity_type: IdentityType | None = None
    relation: str | None = None  # 家屬需填關係
    profile: dict[str, Any] = field(
        default_factory=dict
    )  # 姓名、出生年月日、手機末四碼、院所、科別
    verification_method: VerificationMethod | None = None
    verification_code: str | None = None  # 由驗證閘道產生 / 或出院單代碼
    consent_given: bool = False
    failure_count: int = 0
    escalated: bool = False  # B8 轉真人核對
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RosterEntry:
    """院方識別名單中的一筆病患資料（正式版來自 HIS 匯入）。"""

    id: str
    hospital_id: str
    name: str
    birth_date: str  # YYYY-MM-DD
    phone_last4: str
    department: str | None = None
    discharge_code: str | None = None
    bound_line_user_id: str | None = None


@dataclass
class HandoffTicket:
    id: str
    user_id: str
    reason: str
    status: str = "open"  # open / assigned / closed
    assignee_id: str | None = None
    hospital_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BindingSessionRepository(Protocol):
    def get(self, user_id: str) -> BindingSession | None: ...
    def save(self, session: BindingSession) -> BindingSession: ...
    def delete(self, user_id: str) -> None: ...


class RosterRepository(Protocol):
    def find_match(
        self, *, hospital_id: str, name: str, birth_date: str, phone_last4: str
    ) -> RosterEntry | None: ...
    def save(self, entry: RosterEntry) -> RosterEntry: ...
    def list_by_hospital(self, hospital_id: str) -> list[RosterEntry]: ...


class HandoffRepository(Protocol):
    def create(self, user_id: str, reason: str, hospital_id: str | None) -> HandoffTicket: ...
    def get(self, ticket_id: str) -> HandoffTicket | None: ...
    def save(self, ticket: HandoffTicket) -> HandoffTicket: ...
    def list_open(self, hospital_id: str | None = None) -> list[HandoffTicket]: ...


class InMemoryBindingSessionRepository:
    def __init__(self) -> None:
        self._store: InMemoryStore[BindingSession] = InMemoryStore(lambda s: s.user_id)

    def get(self, user_id: str) -> BindingSession | None:
        return self._store.get(user_id)

    def save(self, session: BindingSession) -> BindingSession:
        session.updated_at = datetime.now(UTC)
        return self._store.save(session)

    def delete(self, user_id: str) -> None:
        self._store.delete(user_id)


class InMemoryRosterRepository:
    def __init__(self) -> None:
        self._store: InMemoryStore[RosterEntry] = InMemoryStore(lambda e: e.id)

    def find_match(
        self, *, hospital_id: str, name: str, birth_date: str, phone_last4: str
    ) -> RosterEntry | None:
        return self._store.find_one(
            lambda e: (
                e.hospital_id == hospital_id
                and e.name == name
                and e.birth_date == birth_date
                and e.phone_last4 == phone_last4
            )
        )

    def save(self, entry: RosterEntry) -> RosterEntry:
        return self._store.save(entry)

    def list_by_hospital(self, hospital_id: str) -> list[RosterEntry]:
        return [e for e in self._store if e.hospital_id == hospital_id]


class InMemoryHandoffRepository:
    def __init__(self) -> None:
        self._store: InMemoryStore[HandoffTicket] = InMemoryStore(lambda t: t.id)

    def create(self, user_id: str, reason: str, hospital_id: str | None) -> HandoffTicket:
        ticket = HandoffTicket(
            id=uuid.uuid4().hex, user_id=user_id, reason=reason, hospital_id=hospital_id
        )
        return self._store.save(ticket)

    def get(self, ticket_id: str) -> HandoffTicket | None:
        return self._store.get(ticket_id)

    def save(self, ticket: HandoffTicket) -> HandoffTicket:
        return self._store.save(ticket)

    def list_open(self, hospital_id: str | None = None) -> list[HandoffTicket]:
        return [
            t
            for t in self._store
            if t.status == "open" and (hospital_id is None or t.hospital_id == hospital_id)
        ]
