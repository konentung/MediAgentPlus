"""病人自填表服務：草稿續填、逐步存檔、送出、送出後轉為待院方核對。

狀態轉換（docs/patient-form-design.md §4.3）：
    送出 intake            UNBOUND  → PENDING
    院方退回               PENDING  → UNBOUND（可再編輯、重新送出）
送出後（PENDING / VERIFIED）病人端一律唯讀：不能存步驟、不能再送出，只能檢視；
資料修正由院方在後台進行（PatientRecordService.correct_profile）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AuthorizationError, ConflictError, ValidationError
from app.domain.enums import BindingStatus
from app.domain.intake_schema import (
    ALL_FIELDS,
    CONSENT_VERSION,
    TOTAL_STEPS,
    get_step,
    public_schema,
    validate_all,
    validate_step,
)
from app.domain.principal import Principal
from app.extensions import db
from app.line.client import LineMessagingClient
from app.line.messages.builder import MessageBuilder as M
from app.repositories.patient_repository import (
    PROFILE_META_KEYS,
    NationalIdCipherText,
    PatientRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.hospital_service import HospitalService
from app.utils.crypto import FieldCipher

logger = logging.getLogger(__name__)

_MASKED_ID = re.compile(r"^\*+\d{4}$")
IDENTITY_STEP = 1
# 送出後病人端唯讀的狀態；退回（UNBOUND）後才能再編輯
READ_ONLY_STATUSES = frozenset({BindingStatus.PENDING, BindingStatus.VERIFIED})
NATIONAL_ID_CONFLICT_MESSAGE = (
    "此身分證字號已在其他 LINE 帳號填寫過。若您是家屬，請由病人本人的帳號填寫；"
    "若您確定是本人，請到院所櫃檯協助處理。"
)
CONSENT_KEYS = tuple(f.key for f in ALL_FIELDS.values() if f.storage == "consent")


class IntakeService:
    def __init__(
        self,
        patients: PatientRepository,
        users: UserRepository,
        hospitals: HospitalService,
        cipher: FieldCipher,
        line_client: Callable[[], LineMessagingClient],
    ) -> None:
        self._patients = patients
        self._users = users
        self._hospitals = hospitals
        self._cipher = cipher
        self._line = line_client

    def get_form_state(self, principal: Principal) -> dict[str, Any]:
        """LIFF 表單頁初始化：schema + 已填資料（遮罩）+ 進度。"""
        profile = self._patients.get_profile(principal.user_id) or {}
        return {
            "steps": public_schema(),
            "total_steps": TOTAL_STEPS,
            "data": self.mask_profile(profile),
            "completed_steps": profile.get("completed_steps") or [],
            "submitted_at": profile.get("submitted_at"),
            "binding_status": principal.binding_status.value,
            "read_only": principal.binding_status in READ_ONLY_STATUSES or principal.is_locked,
        }

    def get_profile(self, principal: Principal) -> dict[str, Any]:
        profile = self._patients.get_profile(principal.user_id)
        return self.mask_profile(profile or {})

    @staticmethod
    def mask_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """移除加密欄位與指紋，只留末四碼的遮罩字串。"""
        data = {k: v for k, v in profile.items() if k not in PROFILE_META_KEYS}
        last4 = profile.get("national_id_last4")
        data["national_id"] = f"******{last4}" if last4 else None
        data["completed_steps"] = profile.get("completed_steps") or []
        data["submitted_at"] = profile.get("submitted_at")
        return data

    def save_step(
        self,
        principal: Principal,
        step_number: int,
        payload: dict[str, Any],
        *,
        partial: bool = True,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """存單一步驟。`partial=True` 只檢查格式不檔必填（前端「下一頁」與「儲存草稿」都用這個），
        必填在 `submit()` 時整體檢查；`partial=False` 保留給需要單步完整驗證的呼叫端。"""
        self._ensure_can_edit(principal)
        step = get_step(step_number)
        existing = self._patients.get_profile(principal.user_id) or {}

        skip: set[str] = set()
        national_id: NationalIdCipherText | None = None
        raw_id = payload.get("national_id")
        if step.number == IDENTITY_STEP:
            if isinstance(raw_id, str) and _MASKED_ID.match(raw_id):
                # 前端回傳遮罩字串代表沒有改動，沿用既有加密值
                payload = {**payload, "national_id": None}
                if existing.get("national_id_fingerprint"):
                    skip.add("national_id")

        cleaned = validate_step(step, payload, partial=partial, skip_required=frozenset(skip))

        if cleaned.get("national_id"):
            national_id = self._encrypt_national_id(principal.user_id, cleaned["national_id"])
            cleaned["national_id"] = None  # 明碼不落 DB、不回前端
        if cleaned.get("preferred_hospital_id"):
            self._hospitals.require_active(cleaned["preferred_hospital_id"])

        try:
            profile = self._patients.save_step(
                principal.user_id, step, cleaned, national_id=national_id
            )
        except IntegrityError as exc:
            # national_id_fingerprint 有唯一索引：兩個帳號同時填同一身分證時由 DB 兜底
            db.session.rollback()
            raise ConflictError(NATIONAL_ID_CONFLICT_MESSAGE) from exc
        # 同意事項：只要這次有送到值（非 None）就記錄一列，草稿也算，送出時才檢查是否同意
        consents = {k: bool(cleaned[k]) for k in CONSENT_KEYS if cleaned.get(k) is not None}
        if consents:
            self._patients.add_consents(
                principal.user_id,
                consents,
                version=CONSENT_VERSION,
                ip_address=ip_address,
                line_user_id=principal.line_user_id,
            )
            profile.update(consents)

        return self.mask_profile(profile)

    def submit(self, principal: Principal) -> dict[str, Any]:
        self._ensure_can_edit(principal)
        profile = self._patients.get_profile(principal.user_id)
        if not profile:
            raise ValidationError("尚未填寫任何資料")

        skip = frozenset({"national_id"}) if profile.get("national_id_fingerprint") else frozenset()
        validate_all(profile, skip_required=skip)
        if not profile.get("consent_data_collection"):
            raise ValidationError("必須同意個人資料蒐集告知事項才能送出")

        snapshot = self.mask_profile(profile)
        snapshot.pop("completed_steps", None)
        snapshot.pop("submitted_at", None)
        submission = self._patients.add_submission(principal.user_id, snapshot)

        if principal.binding_status is not BindingStatus.VERIFIED:
            self._users.update(principal.user_id, binding_status=BindingStatus.PENDING)
        self._notify(
            principal,
            "已收到您的基本資料，院所核對身份後會再通知您。核對完成前仍可使用一般諮詢功能。",
        )
        return {"submission": submission, "binding_status": BindingStatus.PENDING.value}

    def _ensure_can_edit(self, principal: Principal) -> None:
        if not principal.is_authenticated:
            raise AuthorizationError("請先登入")
        if principal.is_locked:
            raise AuthorizationError("帳號已鎖定，請聯絡真人客服協助處理")
        if principal.binding_status in READ_ONLY_STATUSES:
            raise AuthorizationError(
                "資料已送出，目前只能檢視；需要修改請聯絡院所",
                details={"binding_status": principal.binding_status.value, "read_only": True},
            )

    def _encrypt_national_id(self, user_id: str, plaintext: str) -> NationalIdCipherText:
        fingerprint = self._cipher.fingerprint(plaintext)
        owner = self._patients.find_user_id_by_fingerprint(fingerprint)
        if owner is not None and owner != user_id:
            # 同一個身分證已在別的 LINE 帳號：只回衝突，不自動鎖定。
            # 家屬與病人本人各自用自己的 LINE 填同一位病人是合理情境，鎖定會誤傷；
            # 真正的冒用由院方核對身份時判斷，必要時用後台「鎖定」。
            raise ConflictError(NATIONAL_ID_CONFLICT_MESSAGE)
        return NationalIdCipherText(
            encrypted=self._cipher.encrypt(plaintext),
            last4=plaintext[-4:],
            fingerprint=fingerprint,
        )

    def _notify(self, principal: Principal, text: str) -> None:
        """推播失敗只記 log：狀態已寫入 DB，不能因為 LINE API 暫時失敗讓整個請求變 500。"""
        if not principal.line_user_id:
            return
        try:
            self._line().push(principal.line_user_id, [M.text(text)])
        except Exception:
            logger.exception("LINE push failed for user %s", principal.user_id)
