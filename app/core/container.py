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
from app.auth.state_store import InMemoryOAuthStateStore, OAuthStateStore, RedisOAuthStateStore
from app.core.ratelimit import build_limiter
from app.line.client import LineMessagingClient
from app.repositories.audit_repository import AuditRepository, SqlAlchemyAuditRepository
from app.repositories.auth_session_repository import (
    AuthSessionRepository,
    SqlAlchemyAuthSessionRepository,
)
from app.repositories.handoff_repository import HandoffRepository, SqlAlchemyHandoffRepository
from app.repositories.hospital_repository import HospitalRepository, SqlAlchemyHospitalRepository
from app.repositories.patient_record_repository import (
    PatientRecordRepository,
    SqlAlchemyPatientRecordRepository,
)
from app.repositories.patient_repository import PatientRepository, SqlAlchemyPatientRepository
from app.repositories.user_repository import SqlAlchemyUserRepository, UserRepository
from app.services.auth_session_service import AuthSessionService
from app.services.handoff_service import HandoffService
from app.services.hospital_service import HospitalService
from app.services.intake_service import IntakeService
from app.services.patient_record_service import PatientRecordService
from app.services.user_service import UserService
from app.utils.crypto import FieldCipher


@dataclass
class ServiceContainer:
    # repositories
    user_repo: UserRepository
    audit_repo: AuditRepository
    auth_session_repo: AuthSessionRepository
    handoff_repo: HandoffRepository
    hospital_repo: HospitalRepository
    patient_repo: PatientRepository
    patient_record_repo: PatientRecordRepository
    state_store: OAuthStateStore
    # services
    users: UserService
    handoff: HandoffService
    hospitals: HospitalService
    intake: IntakeService
    patient_records: PatientRecordService
    cipher: FieldCipher
    jwt: JwtService
    auth_sessions: AuthSessionService
    line_login: LineLoginService
    liff_auth: LiffAuthService
    line_client: LineMessagingClient

    @classmethod
    def build(cls, app: Flask) -> ServiceContainer:
        cfg = app.config
        user_repo = SqlAlchemyUserRepository()
        audit_repo = SqlAlchemyAuditRepository()
        handoff_repo = SqlAlchemyHandoffRepository()
        hospital_repo = SqlAlchemyHospitalRepository()
        patient_repo = SqlAlchemyPatientRepository()
        patient_record_repo = SqlAlchemyPatientRecordRepository()
        auth_session_repo = SqlAlchemyAuthSessionRepository()
        redis_client = _build_redis(cfg.get("REDIS_URL"))
        state_store: OAuthStateStore = (
            RedisOAuthStateStore(redis_client) if redis_client else InMemoryOAuthStateStore()
        )
        app.extensions["rate_limiter"] = build_limiter(redis_client)
        cipher = FieldCipher(cfg.get("PATIENT_DATA_KEY"), fallback_secret=cfg["SECRET_KEY"])

        hospitals = HospitalService(hospital_repo)
        users = UserService(user_repo, hospitals)
        handoff = HandoffService(handoff_repo)
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
        auth_sessions = AuthSessionService(
            auth_session_repo, jwt_service, line_login, cipher, cfg["JWT_REFRESH_TOKEN_EXPIRES"]
        )
        # Service 透過 getter 取用 line_client，測試替換 container.line_client 時才會一起生效
        intake = IntakeService(
            patient_repo, user_repo, hospitals, cipher, lambda: container.line_client
        )
        patient_records = PatientRecordService(
            patient_repo,
            patient_record_repo,
            user_repo,
            hospitals,
            audit_repo,
            cipher,
            lambda: container.line_client,
            revoke_sessions=auth_sessions.revoke_all,
        )

        container = cls(
            user_repo=user_repo,
            audit_repo=audit_repo,
            auth_session_repo=auth_session_repo,
            handoff_repo=handoff_repo,
            hospital_repo=hospital_repo,
            patient_repo=patient_repo,
            patient_record_repo=patient_record_repo,
            state_store=state_store,
            users=users,
            handoff=handoff,
            hospitals=hospitals,
            intake=intake,
            patient_records=patient_records,
            cipher=cipher,
            jwt=jwt_service,
            auth_sessions=auth_sessions,
            line_login=line_login,
            liff_auth=liff_auth,
            line_client=line_client,
        )
        return container


def get_container() -> ServiceContainer:
    return current_app.extensions["container"]


def _build_redis(url: str | None):
    """有設定 REDIS_URL 才建立連線；套件缺少或連不上會在啟動時立即失敗，不默默退回記憶體。"""
    if not url:
        return None
    import redis

    client = redis.Redis.from_url(url, decode_responses=True)
    client.ping()
    return client
