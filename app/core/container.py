"""ServiceContainer：簡易依賴注入容器，集中組裝 Repository / Service / 外部客戶端。

測試時可直接替換容器內任何元件（例如假的 LineMessagingClient）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, current_app

from app.auth.jwt_service import JwtService
from app.auth.liff_auth import LiffAuthService
from app.auth.line_identity import LineIdentityVerifier
from app.auth.line_login import LineLoginService
from app.auth.state_store import InMemoryOAuthStateStore, OAuthStateStore, RedisOAuthStateStore
from app.core.ratelimit import build_limiter
from app.integrations.llm import DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL, LlmClient, OpenAiClient
from app.integrations.nhi_er import DEFAULT_API_URL as DEFAULT_ER_API_URL
from app.integrations.nhi_er import ErClient, NhiErClient
from app.integrations.vision import LlmVisionClient, VisionClient
from app.line.client import LineMessagingClient
from app.line.state_store import (
    ConversationStateStore,
    InMemoryConversationStateStore,
    RedisConversationStateStore,
)
from app.repositories.audit_repository import AuditRepository, SqlAlchemyAuditRepository
from app.repositories.feedback_repository import FeedbackRepository, SqlAlchemyFeedbackRepository
from app.repositories.hospital_repository import HospitalRepository, SqlAlchemyHospitalRepository
from app.repositories.knowledge_repository import (
    KnowledgeJobRepository,
    KnowledgeRepository,
    SqlAlchemyKnowledgeJobRepository,
    SqlAlchemyKnowledgeRepository,
)
from app.repositories.patient_record_repository import (
    PatientRecordRepository,
    SqlAlchemyPatientRecordRepository,
)
from app.repositories.patient_repository import PatientRepository, SqlAlchemyPatientRepository
from app.repositories.pharmacy_repository import (
    DrugRepository,
    PharmacyRepository,
    SqlAlchemyDrugRepository,
    SqlAlchemyPharmacyRepository,
)
from app.repositories.prescription_repository import (
    PrescriptionRepository,
    SqlAlchemyPrescriptionRepository,
)
from app.repositories.reminder_repository import (
    DeliveryRepository,
    ScheduleRepository,
    SqlAlchemyDeliveryRepository,
    SqlAlchemyScheduleRepository,
)
from app.repositories.triage_repository import SnapshotRepository, SqlAlchemySnapshotRepository
from app.repositories.user_repository import SqlAlchemyUserRepository, UserRepository
from app.services.conversation_service import ConversationService
from app.services.feedback_service import FeedbackService
from app.services.hospital_service import HospitalService
from app.services.intake_service import IntakeService
from app.services.knowledge_job_service import KnowledgeJobService
from app.services.knowledge_service import KnowledgeService
from app.services.patient_record_service import PatientRecordService
from app.services.pharmacy_service import PharmacyService
from app.services.prescription_service import PrescriptionService
from app.services.reminder_service import ReminderService
from app.services.triage_service import TriageService
from app.services.user_service import UserService
from app.utils.crypto import FieldCipher


@dataclass
class ServiceContainer:
    # repositories
    user_repo: UserRepository
    audit_repo: AuditRepository
    feedback_repo: FeedbackRepository
    hospital_repo: HospitalRepository
    patient_repo: PatientRepository
    patient_record_repo: PatientRecordRepository
    prescription_repo: PrescriptionRepository
    pharmacy_repo: PharmacyRepository
    drug_repo: DrugRepository
    schedule_repo: ScheduleRepository
    delivery_repo: DeliveryRepository
    snapshot_repo: SnapshotRepository
    knowledge_repo: KnowledgeRepository
    knowledge_job_repo: KnowledgeJobRepository
    state_store: OAuthStateStore
    conversation_state: ConversationStateStore
    # services
    users: UserService
    feedback: FeedbackService
    hospitals: HospitalService
    intake: IntakeService
    patient_records: PatientRecordService
    pharmacies: PharmacyService
    prescriptions: PrescriptionService
    reminders: ReminderService
    triage: TriageService
    knowledge: KnowledgeService
    knowledge_jobs: KnowledgeJobService
    conversation: ConversationService
    vision_client: VisionClient
    llm_client: LlmClient
    er_client: ErClient
    cipher: FieldCipher
    jwt: JwtService
    line_login: LineLoginService
    liff_auth: LiffAuthService
    line_client: LineMessagingClient

    @classmethod
    def build(cls, app: Flask) -> ServiceContainer:
        cfg = app.config
        user_repo = SqlAlchemyUserRepository()
        audit_repo = SqlAlchemyAuditRepository()
        feedback_repo = SqlAlchemyFeedbackRepository()
        hospital_repo = SqlAlchemyHospitalRepository()
        patient_repo = SqlAlchemyPatientRepository()
        patient_record_repo = SqlAlchemyPatientRecordRepository()
        prescription_repo = SqlAlchemyPrescriptionRepository()
        pharmacy_repo = SqlAlchemyPharmacyRepository()
        drug_repo = SqlAlchemyDrugRepository()
        schedule_repo = SqlAlchemyScheduleRepository()
        delivery_repo = SqlAlchemyDeliveryRepository()
        snapshot_repo = SqlAlchemySnapshotRepository()
        knowledge_repo = SqlAlchemyKnowledgeRepository()
        knowledge_job_repo = SqlAlchemyKnowledgeJobRepository()
        redis_client = _build_redis(cfg.get("REDIS_URL"))
        state_store: OAuthStateStore = (
            RedisOAuthStateStore(redis_client) if redis_client else InMemoryOAuthStateStore()
        )
        conversation_state: ConversationStateStore = (
            RedisConversationStateStore(redis_client)
            if redis_client
            else InMemoryConversationStateStore()
        )
        app.extensions["rate_limiter"] = build_limiter(redis_client)
        cipher = FieldCipher(cfg.get("PATIENT_DATA_KEY"), fallback_secret=cfg["SECRET_KEY"])

        hospitals = HospitalService(hospital_repo)
        users = UserService(user_repo, hospitals, _csv_set(cfg.get("SUPER_ADMIN_LINE_USER_IDS")))
        feedback = FeedbackService(feedback_repo, patient_repo)
        jwt_service = JwtService(
            secret=cfg["JWT_SECRET_KEY"],
            algorithm=cfg["JWT_ALGORITHM"],
            access_expires=cfg["JWT_ACCESS_TOKEN_EXPIRES"],
            staff_access_expires=cfg["JWT_STAFF_ACCESS_TOKEN_EXPIRES"],
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
            lambda: container.line_client,
        )

        openai_api_key = cfg.get("OPENAI_API_KEY", "")
        vision_client = LlmVisionClient(
            OpenAiClient(
                cfg.get("OCR_MODEL") or DEFAULT_VISION_MODEL,
                openai_api_key,
                int(cfg.get("OCR_TIMEOUT_SECONDS", 90)),
            )
        )
        pharmacies = PharmacyService(pharmacy_repo, drug_repo)
        prescriptions = PrescriptionService(
            prescription_repo,
            pharmacies,
            lambda: container.vision_client,
            cipher,
            cfg.get("MEDIA_ROOT", "var/media"),
            int(cfg.get("REFILL_REMINDER_DAYS_BEFORE", 3)),
        )
        reminders = ReminderService(
            schedule_repo,
            delivery_repo,
            prescription_repo,
            user_repo,
            lambda: container.line_client,
            liff_id=cfg.get("LIFF_ID", ""),
            window_minutes=int(cfg.get("REMINDER_WINDOW_MINUTES", 15)),
        )

        # 急診分流（找醫院）：健保署急診即時 API；測試替換 container.er_client
        er_client = NhiErClient(
            cfg.get("NHI_ER_API_URL") or DEFAULT_ER_API_URL,
            timeout_seconds=int(cfg.get("NHI_ER_TIMEOUT_SECONDS", 20)),
        )
        triage = TriageService(
            snapshot_repo,
            hospital_repo,
            lambda: container.er_client,
            liff_id=cfg.get("LIFF_ID", ""),
            stale_minutes=int(cfg.get("TRIAGE_STALE_MINUTES", 30)),
            retention_days=int(cfg.get("TRIAGE_RETENTION_DAYS", 30)),
        )

        llm_client = OpenAiClient(
            cfg.get("AI_MODEL") or DEFAULT_TEXT_MODEL,
            openai_api_key,
            int(cfg.get("AI_TIMEOUT_SECONDS", 60)),
            embedding_model=cfg.get("EMBEDDING_MODEL") or None,
        )
        knowledge = KnowledgeService(
            knowledge_repo,
            lambda: container.llm_client,
            top_k=int(cfg.get("KNOWLEDGE_TOP_K", 4)),
        )
        knowledge_jobs = KnowledgeJobService(
            knowledge_job_repo,
            knowledge,
            Path(cfg.get("MEDIA_ROOT", "var/media")) / "knowledge-imports",
            spawn=_background_thread(app),
        )
        conversation = ConversationService(
            knowledge,
            pharmacies,
            triage,
            patient_repo,
            conversation_state,
            lambda: container.llm_client,
            records=patient_record_repo,
            enabled=bool(cfg.get("AI_ENABLED", True)),
            history_turns=int(cfg.get("AI_HISTORY_TURNS", 6)),
            history_ttl=int(cfg.get("AI_HISTORY_TTL_SECONDS", 1800)),
            top_k=int(cfg.get("KNOWLEDGE_TOP_K", 4)),
        )

        container = cls(
            user_repo=user_repo,
            audit_repo=audit_repo,
            feedback_repo=feedback_repo,
            hospital_repo=hospital_repo,
            patient_repo=patient_repo,
            patient_record_repo=patient_record_repo,
            prescription_repo=prescription_repo,
            pharmacy_repo=pharmacy_repo,
            drug_repo=drug_repo,
            schedule_repo=schedule_repo,
            delivery_repo=delivery_repo,
            snapshot_repo=snapshot_repo,
            knowledge_repo=knowledge_repo,
            knowledge_job_repo=knowledge_job_repo,
            state_store=state_store,
            conversation_state=conversation_state,
            users=users,
            feedback=feedback,
            hospitals=hospitals,
            intake=intake,
            patient_records=patient_records,
            pharmacies=pharmacies,
            prescriptions=prescriptions,
            reminders=reminders,
            triage=triage,
            knowledge=knowledge,
            knowledge_jobs=knowledge_jobs,
            conversation=conversation,
            vision_client=vision_client,
            llm_client=llm_client,
            er_client=er_client,
            cipher=cipher,
            jwt=jwt_service,
            line_login=line_login,
            liff_auth=liff_auth,
            line_client=line_client,
        )
        return container


def get_container() -> ServiceContainer:
    return current_app.extensions["container"]


def _csv_set(value: str | None) -> frozenset[str]:
    return frozenset(item.strip() for item in (value or "").split(",") if item.strip())


def _build_redis(url: str | None):
    """有設定 REDIS_URL 才建立連線；套件缺少或連不上會在啟動時立即失敗，不默默退回記憶體。"""
    if not url:
        return None
    import redis

    client = redis.Redis.from_url(url, decode_responses=True)
    client.ping()
    return client


def _background_thread(app: Flask) -> Callable[[Callable[[], None]], None]:
    """在背景 daemon 執行緒內帶著 app context 執行。

    測試可把 `container.knowledge_jobs.spawn` 換成同步呼叫。
    """

    def spawn(fn: Callable[[], None]) -> None:
        def target() -> None:
            with app.app_context():
                fn()

        threading.Thread(target=target, name="knowledge-job", daemon=True).start()

    return spawn
