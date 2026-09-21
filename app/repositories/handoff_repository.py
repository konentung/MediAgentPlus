"""真人客服工單 Repository（找真人 → 轉真人客服）。

`HandoffTicket` 是 Service 層使用的資料結構；`HandoffRepository` Protocol 定義介面，
`InMemoryHandoffRepository` 與 `SqlAlchemyHandoffRepository`（對應 `HandoffTicketModel`）
皆實作同一介面。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.extensions import db
from app.models.handoff import HandoffTicketModel
from app.repositories.base import InMemoryStore


@dataclass
class HandoffTicket:
    id: str
    user_id: str
    reason: str
    status: str = "open"  # open / assigned / closed
    assignee_id: str | None = None
    hospital_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class HandoffRepository(Protocol):
    def create(self, user_id: str, reason: str, hospital_id: str | None) -> HandoffTicket: ...
    def get(self, ticket_id: str) -> HandoffTicket | None: ...
    def save(self, ticket: HandoffTicket) -> HandoffTicket: ...
    def list_open(self, hospital_id: str | None = None) -> list[HandoffTicket]: ...


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


def _to_dataclass(row: HandoffTicketModel) -> HandoffTicket:
    return HandoffTicket(
        id=row.id,
        user_id=row.user_id,
        reason=row.reason,
        status=row.status,
        assignee_id=row.assignee_id,
        hospital_id=row.hospital_id,
        created_at=row.created_at,
    )


class SqlAlchemyHandoffRepository:
    def create(self, user_id: str, reason: str, hospital_id: str | None) -> HandoffTicket:
        row = HandoffTicketModel(
            id=uuid.uuid4().hex, user_id=user_id, reason=reason, hospital_id=hospital_id
        )
        db.session.add(row)
        db.session.commit()
        return _to_dataclass(row)

    def get(self, ticket_id: str) -> HandoffTicket | None:
        row = db.session.get(HandoffTicketModel, ticket_id)
        return _to_dataclass(row) if row else None

    def save(self, ticket: HandoffTicket) -> HandoffTicket:
        row = db.session.get(HandoffTicketModel, ticket.id)
        if row is None:
            row = HandoffTicketModel(id=ticket.id, created_at=ticket.created_at)
            db.session.add(row)
        row.user_id = ticket.user_id
        row.reason = ticket.reason
        row.status = ticket.status
        row.assignee_id = ticket.assignee_id
        row.hospital_id = ticket.hospital_id
        db.session.commit()
        return _to_dataclass(row)

    def list_open(self, hospital_id: str | None = None) -> list[HandoffTicket]:
        stmt = db.select(HandoffTicketModel).where(HandoffTicketModel.status == "open")
        if hospital_id is not None:
            stmt = stmt.where(HandoffTicketModel.hospital_id == hospital_id)
        rows = db.session.execute(stmt).scalars()
        return [_to_dataclass(row) for row in rows]
