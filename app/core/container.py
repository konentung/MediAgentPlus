"""ServiceContainer：簡易依賴注入容器，集中組裝 Repository / Service / 外部客戶端。

測試時可直接替換容器內任何元件（例如假的 LineMessagingClient）。
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from app.auth.jwt_service import JwtService
from app.auth.liff_auth import LiffAuthService
from app.auth.line_identity import LineIdentityVerifier
from app.auth.line_login import LineLoginService
from app.auth.state_store import InMemoryOAuthStateStore, OAuthStateStore
from app.line.client import LineMessagingClient
from app.repositories.binding_repository import (
    BindingSessionRepository,
    HandoffRepository,
    InMemoryBindingSessionRepository,
    InMemoryHandoffRepository,
    InMemoryRosterRepository,
    RosterRepository,
)
from app.repositories.user_repository import InMemoryUserRepository, UserRepository
from app.services.binding_service import BindingService
from app.services.handoff_service import HandoffService
from app.services.user_service import UserService
from app.services.verification import StubVerificationGateway, VerificationGateway


@dataclass
class ServiceContainer:
    # repositories
    user_repo: UserRepository
    binding_repo: BindingSessionRepository
    roster_repo: RosterRepository
    handoff_repo: HandoffRepository
    state_store: OAuthStateStore
    # services
    users: UserService
    handoff: HandoffService
    binding: BindingService
    verification: VerificationGateway
    jwt: JwtService
    line_login: LineLoginService
    liff_auth: LiffAuthService
    line_client: LineMessagingClient

    @classmethod
    def build(cls, app: Flask) -> ServiceContainer:
        cfg = app.config
        user_repo = InMemoryUserRepository()
        binding_repo = InMemoryBindingSessionRepository()
        roster_repo = InMemoryRosterRepository()
        handoff_repo = InMemoryHandoffRepository()
        state_store = InMemoryOAuthStateStore()

        users = UserService(user_repo)
        handoff = HandoffService(handoff_repo)
        verification = StubVerificationGateway()
        binding = BindingService(
            binding_repo,
            roster_repo,
            users,
            handoff,
            verification,
            max_failures=cfg["BINDING_MAX_FAILURES"],
        )
        jwt_service = JwtService(
            secret=cfg["JWT_SECRET_KEY"],
            algorithm=cfg["JWT_ALGORITHM"],
            access_expires=cfg["JWT_ACCESS_TOKEN_EXPIRES"],
            refresh_expires=cfg["JWT_REFRESH_TOKEN_EXPIRES"],
        )
        login_verifier = LineIdentityVerifier(cfg["LINE_LOGIN_CHANNEL_ID"])
        line_login = LineLoginService(
            channel_id=cfg["LINE_LOGIN_CHANNEL_ID"],
            channel_secret=cfg["LINE_LOGIN_CHANNEL_SECRET"],
            redirect_uri=cfg["LINE_LOGIN_REDIRECT_URI"],
            scope=cfg["LINE_LOGIN_SCOPE"],
            state_store=state_store,
            state_ttl_seconds=cfg["OAUTH_STATE_TTL_SECONDS"],
            verifier=login_verifier,
        )
        liff_auth = LiffAuthService(
            LineIdentityVerifier(cfg["LIFF_CHANNEL_ID"]), liff_id=cfg["LIFF_ID"]
        )
        line_client = LineMessagingClient(cfg["LINE_CHANNEL_ACCESS_TOKEN"])

        return cls(
            user_repo=user_repo,
            binding_repo=binding_repo,
            roster_repo=roster_repo,
            handoff_repo=handoff_repo,
            state_store=state_store,
            users=users,
            handoff=handoff,
            binding=binding,
            verification=verification,
            jwt=jwt_service,
            line_login=line_login,
            liff_auth=liff_auth,
            line_client=line_client,
        )


def get_container() -> ServiceContainer:
    return current_app.extensions["container"]
