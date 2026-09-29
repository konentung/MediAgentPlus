"""意見回饋：使用者從 LIFF 表單送出（不需填過病人表單），院方後台查看與標記處理。

沒有真人客服：Bot 不會因為使用者送回饋而閉嘴，回饋只是留言給院方 / 開發者看。
歸屬院所 = 病人在 LINE 上確認過的就醫院所（`preferred_hospital_id` + `hospital_confirmed_at`），
院方帳號則用自己的 `hospital_id`；都沒有就只有 SUPER_ADMIN 看得到。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.patient_repository import PatientRepository

CATEGORIES: dict[str, str] = {
    "bug": "功能問題",
    "content": "內容有誤",
    "suggestion": "建議",
    "other": "其他",
}
STATUSES: tuple[str, ...] = ("open", "resolved")
MIN_CONTENT = 5
MAX_CONTENT = 2000
MAX_CONTACT = 120


class FeedbackService:
    def __init__(self, feedback: FeedbackRepository, patients: PatientRepository) -> None:
        self._feedback = feedback
        self._patients = patients

    def submit(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        category = str(payload.get("category") or "").strip()
        content = str(payload.get("content") or "").strip()
        contact = str(payload.get("contact") or "").strip() or None
        if category not in CATEGORIES:
            raise ValidationError("請選擇回饋類別", details={"field": "category"})
        if not MIN_CONTENT <= len(content) <= MAX_CONTENT:
            raise ValidationError(
                f"內容請介於 {MIN_CONTENT}–{MAX_CONTENT} 字", details={"field": "content"}
            )
        if contact and len(contact) > MAX_CONTACT:
            raise ValidationError("聯絡方式過長", details={"field": "contact"})
        return self._feedback.create(
            user_id=principal.user_id,
            display_name=principal.display_name,
            hospital_id=self._hospital_for(principal),
            category=category,
            content=content,
            contact=contact,
        )

    def list_mine(self, principal: Principal) -> list[dict[str, Any]]:
        return self._feedback.list_for_user(principal.user_id) if principal.user_id else []

    def list(self, actor: Principal, status: str | None = None) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise ValidationError("status 只能是 open 或 resolved")
        return self._feedback.list(hospital_id=AccessPolicy.hospital_scope(actor), status=status)

    def count_open(self, actor: Principal) -> int:
        return self._feedback.count_open(AccessPolicy.hospital_scope(actor))

    def set_status(
        self, actor: Principal, feedback_id: str, status: str, note: str | None = None
    ) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValidationError("status 只能是 open 或 resolved")
        item = self._feedback.get(feedback_id)
        if item is None:
            raise NotFoundError("回饋不存在")
        scope = AccessPolicy.hospital_scope(actor)
        if scope is not None and item["hospital_id"] != scope:
            raise AuthorizationError("不得處理其他院所的回饋")
        resolved = status == "resolved"
        return self._feedback.update(
            feedback_id,
            status=status,
            resolved_by=actor.user_id if resolved else None,
            resolved_at=datetime.now(UTC) if resolved else None,
            resolver_note=str(note or "").strip()[:MAX_CONTENT] or None,  # 修改：可能不是字串
        )

    def _hospital_for(self, principal: Principal) -> str | None:
        if principal.hospital_id:
            return principal.hospital_id
        profile = self._patients.get_profile(principal.user_id) if principal.user_id else None
        if profile and profile.get("hospital_confirmed_at"):
            return profile.get("preferred_hospital_id")
        return None
