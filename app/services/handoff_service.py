"""轉真人客服服務（任何時候一鍵可按）。"""

from __future__ import annotations

from app.core.exceptions import AuthorizationError, NotFoundError
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.handoff_repository import HandoffRepository, HandoffTicket


class HandoffService:
    def __init__(self, tickets: HandoffRepository) -> None:
        self._tickets = tickets

    def request(self, principal: Principal, reason: str = "user_request") -> HandoffTicket:
        """使用者端發起；未登入者亦可（以 line_user_id 為主體）。"""
        subject = principal.user_id or principal.line_user_id or "anonymous"
        return self._tickets.create(subject, reason, principal.hospital_id)

    def list_open(self, actor: Principal) -> list[HandoffTicket]:
        hospital_scope = (
            None if AccessPolicy.can_access_hospital(actor, None) else actor.hospital_id
        )
        return self._tickets.list_open(hospital_scope)

    def accept(self, actor: Principal, ticket_id: str) -> HandoffTicket:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise NotFoundError("工單不存在")
        if (
            not AccessPolicy.can_access_hospital(actor, ticket.hospital_id)
            and ticket.hospital_id is not None
        ):
            raise AuthorizationError("不得處理其他院所的工單")
        ticket.status = "assigned"
        ticket.assignee_id = actor.user_id
        return self._tickets.save(ticket)
