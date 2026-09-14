"""綁定流程狀態機（服務地圖 B1–B9）。

B1 開啟 LIFF 表單 → B2 身分別 → B3 基本欄位 → B4 驗證方式 → B5 同意書
→ 與院方識別名單比對 → B7 完成 / B8 連續 3 次失敗轉真人 / B9 已被其他 LINE 綁定 → 鎖定
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.exceptions import ConflictError, ValidationError
from app.domain.enums import BindingStatus, IdentityType, Role, VerificationMethod
from app.domain.principal import Principal
from app.repositories.binding_repository import (
    BindingSession,
    BindingSessionRepository,
    BindingStep,
    RosterRepository,
)
from app.services.handoff_service import HandoffService
from app.services.user_service import UserService
from app.services.verification import VerificationGateway

REQUIRED_PROFILE_FIELDS = ("name", "birth_date", "phone_last4", "hospital_id")
OPTIONAL_PROFILE_FIELDS = ("department",)


class BindingOutcome(StrEnum):
    VERIFIED = "verified"  # B7
    MISMATCH = "mismatch"  # B8 提示重填
    ESCALATED = "escalated"  # B8 連續 3 次失敗轉真人核對
    LOCKED = "locked"  # B9 已被其他 LINE 綁定


@dataclass(frozen=True)
class BindingResult:
    outcome: BindingOutcome
    failure_count: int
    message: str


class BindingService:
    def __init__(
        self,
        sessions: BindingSessionRepository,
        roster: RosterRepository,
        users: UserService,
        handoff: HandoffService,
        verification: VerificationGateway,
        max_failures: int = 3,
    ) -> None:
        self._sessions = sessions
        self._roster = roster
        self._users = users
        self._handoff = handoff
        self._verification = verification
        self._max_failures = max_failures

    # ---- B1 ----
    def start(self, principal: Principal) -> BindingSession:
        self._ensure_can_bind(principal)
        session = self._sessions.get(principal.user_id) or BindingSession(user_id=principal.user_id)  # type: ignore[arg-type]
        self._users.set_binding_status(principal.user_id, BindingStatus.PENDING)  # type: ignore[arg-type]
        return self._sessions.save(session)

    # ---- B2 ----
    def submit_identity(
        self, principal: Principal, identity_type: IdentityType, relation: str | None
    ) -> BindingSession:
        session = self._require_session(principal, BindingStep.IDENTITY)
        if identity_type is IdentityType.FAMILY and not relation:
            raise ValidationError("家屬需填寫與病患的關係")
        session.identity_type = identity_type
        session.relation = relation if identity_type is IdentityType.FAMILY else None
        session.step = BindingStep.PROFILE
        return self._sessions.save(session)

    # ---- B3 ----
    def submit_profile(self, principal: Principal, profile: dict[str, Any]) -> BindingSession:
        session = self._require_session(principal, BindingStep.PROFILE, BindingStep.VERIFICATION)
        missing = [f for f in REQUIRED_PROFILE_FIELDS if not profile.get(f)]
        if missing:
            raise ValidationError("缺少必填欄位", details={"missing": missing})
        if len(str(profile["phone_last4"])) != 4 or not str(profile["phone_last4"]).isdigit():
            raise ValidationError("手機末四碼格式錯誤")
        session.profile = {
            k: profile[k] for k in REQUIRED_PROFILE_FIELDS + OPTIONAL_PROFILE_FIELDS if k in profile
        }
        session.step = BindingStep.VERIFICATION
        return self._sessions.save(session)

    # ---- B4 ----
    def request_verification(
        self, principal: Principal, method: VerificationMethod, discharge_code: str | None = None
    ) -> BindingSession:
        session = self._require_session(principal, BindingStep.VERIFICATION)
        if method is VerificationMethod.DISCHARGE_CODE and not discharge_code:
            raise ValidationError("請輸入出院單一次性代碼")
        session.verification_method = method
        session.verification_code = self._verification.issue(
            method, phone_last4=session.profile.get("phone_last4"), discharge_code=discharge_code
        )
        session.step = BindingStep.CONSENT
        return self._sessions.save(session)

    # ---- B5 ----
    def give_consent(self, principal: Principal, accepted: bool) -> BindingSession:
        session = self._require_session(principal, BindingStep.CONSENT)
        if not accepted:
            raise ValidationError("需同意資料使用範圍與保存期限才能完成綁定")
        session.consent_given = True
        session.step = BindingStep.MATCHING
        return self._sessions.save(session)

    # ---- 比對 → B7 / B8 / B9 ----
    def verify(self, principal: Principal, code: str) -> BindingResult:
        session = self._require_session(principal, BindingStep.MATCHING)
        entry = self._roster.find_match(
            hospital_id=session.profile["hospital_id"],
            name=session.profile["name"],
            birth_date=session.profile["birth_date"],
            phone_last4=session.profile["phone_last4"],
        )
        code_ok = self._code_matches(session, entry.discharge_code if entry else None, code)

        if entry is None or not code_ok:
            return self._on_failure(principal, session)

        if entry.bound_line_user_id and entry.bound_line_user_id != principal.line_user_id:
            return self._on_locked(principal, session)

        # B7 綁定完成
        entry.bound_line_user_id = principal.line_user_id
        self._roster.save(entry)
        role = Role.FAMILY if session.identity_type is IdentityType.FAMILY else Role.PATIENT
        self._users.set_binding_status(
            principal.user_id,  # type: ignore[arg-type]
            BindingStatus.VERIFIED,
            role=role,
            identity_type=session.identity_type,
            hospital_id=entry.hospital_id,
        )
        session.step = BindingStep.DONE
        self._sessions.save(session)
        return BindingResult(BindingOutcome.VERIFIED, session.failure_count, "綁定完成")

    # ---- 解除綁定（設定頁，二次確認由前端 / LIFF 負責） ----
    def revoke(self, principal: Principal) -> None:
        self._users.set_binding_status(
            principal.user_id,  # type: ignore[arg-type]
            BindingStatus.UNBOUND,
            role=Role.GUEST,
            identity_type=None,
            hospital_id=None,
        )
        self._sessions.delete(principal.user_id)  # type: ignore[arg-type]

    def current(self, principal: Principal) -> BindingSession | None:
        return self._sessions.get(principal.user_id) if principal.user_id else None

    # ---- 內部 ----
    def _ensure_can_bind(self, principal: Principal) -> None:
        if not principal.is_authenticated:
            raise ValidationError("需先登入")
        if principal.is_locked:
            raise ConflictError("此帳號已鎖定，請聯絡真人客服")
        if principal.is_verified:
            raise ConflictError("已完成綁定，若要更換請先解除綁定")

    def _require_session(self, principal: Principal, *allowed: BindingStep) -> BindingSession:
        session = self.current(principal)
        if session is None:
            raise ValidationError("尚未開始綁定流程")
        if session.escalated:
            raise ConflictError("已轉交真人核對，請等待客服聯繫")
        if session.step not in allowed:
            raise ValidationError(f"目前步驟為 {session.step.value}，無法執行此操作")
        return session

    @staticmethod
    def _code_matches(
        session: BindingSession, roster_discharge_code: str | None, code: str
    ) -> bool:
        if session.verification_method is VerificationMethod.DISCHARGE_CODE:
            return bool(roster_discharge_code) and code == roster_discharge_code
        return bool(session.verification_code) and code == session.verification_code

    def _on_failure(self, principal: Principal, session: BindingSession) -> BindingResult:
        session.failure_count += 1
        if session.failure_count >= self._max_failures:
            session.escalated = True
            self._sessions.save(session)
            self._handoff.request(principal, reason="binding_failed_3_times")
            return BindingResult(
                BindingOutcome.ESCALATED, session.failure_count, "已轉真人客服協助核對"
            )
        # 退回 B3 讓使用者重填
        session.step = BindingStep.PROFILE
        self._sessions.save(session)
        return BindingResult(
            BindingOutcome.MISMATCH, session.failure_count, "查無或資料不符，請重新填寫"
        )

    def _on_locked(self, principal: Principal, session: BindingSession) -> BindingResult:
        self._users.set_binding_status(principal.user_id, BindingStatus.LOCKED)  # type: ignore[arg-type]
        session.escalated = True
        self._sessions.save(session)
        self._handoff.request(principal, reason="already_bound_by_other_line")
        return BindingResult(
            BindingOutcome.LOCKED,
            session.failure_count,
            "此資料已被其他 LINE 帳號綁定，已轉真人處理",
        )
