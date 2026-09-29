"""院方病人紀錄服務：列表、詳情、核對身份（建檔）、退回、修正自填資料、就診 / 風險 / 追蹤 / 備註。

所有操作都先過 `AccessPolicy.can_access_hospital()`；院所只看得到「指定本院為常去院所的待審核病人」
與「已在本院建檔的病人」。SUPER_ADMIN 不受限，但寫入時必須指定 hospital_id。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.domain.enums import BindingStatus, Role
from app.domain.intake_schema import ALL_FIELDS, validate_step
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.line.client import LineMessagingClient
from app.line.messages import common
from app.line.messages.builder import MessageBuilder as M
from app.repositories.audit_repository import AuditRepository
from app.repositories.patient_record_repository import CHILD_MODELS, PatientRecordRepository
from app.repositories.patient_repository import (
    PatientRepository,
    PatientSearch,
    steps_for_keys,
)
from app.repositories.user_repository import UserRecord, UserRepository
from app.services.hospital_service import HospitalService
from app.services.intake_service import IntakeService
from app.utils.clock import today_tw

logger = logging.getLogger(__name__)

LIST_STATUS_FILTERS: dict[str, tuple[BindingStatus, ...]] = {
    "pending": (BindingStatus.PENDING,),
    "active": (BindingStatus.VERIFIED,),
    "locked": (BindingStatus.LOCKED,),
    "all": (),
}
IDENTITY_METHODS = ("nhi_card", "id_card", "in_person", "phone")
ENCOUNTER_TYPES = ("opd", "er", "inpatient")
FLAG_TYPES = ("fall", "allergy", "infection", "cognition", "self_harm", "other")
FLAG_LEVELS = ("low", "medium", "high")
FOLLOW_UP_KINDS = ("revisit", "exam", "call", "dressing", "other")
FOLLOW_UP_STATUSES = ("open", "done", "cancelled")
NOTE_VISIBILITY = ("staff_only", "patient_visible")
# 院方修正自填資料時不允許碰的欄位（身份欄位需病人本人重填）
NON_CORRECTABLE = frozenset(
    {"national_id", "filler_relation", "preferred_hospital_id"}
) | frozenset(f.key for f in ALL_FIELDS.values() if f.storage == "consent")


def _require_choice(value: Any, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValidationError(f"{name} 不合法", details={"allowed": list(allowed)})
    return value


class PatientRecordService:
    def __init__(
        self,
        patients: PatientRepository,
        records: PatientRecordRepository,
        users: UserRepository,
        hospitals: HospitalService,
        audit: AuditRepository,
        line_client: Callable[[], LineMessagingClient],
    ) -> None:
        self._patients = patients
        self._records = records
        self._users = users
        self._hospitals = hospitals
        self._audit = audit
        self._line = line_client

    def list_patients(
        self, actor: Principal, *, status: str = "pending", query: str | None = None
    ) -> list[dict[str, Any]]:
        statuses = LIST_STATUS_FILTERS.get(status)
        if statuses is None:
            raise ValidationError("status 不合法", details={"allowed": list(LIST_STATUS_FILTERS)})
        return self._patients.search(
            PatientSearch(
                hospital_id=self._scope(actor), binding_statuses=statuses, query=query or None
            )
        )

    def get_patient(self, actor: Principal, user_id: str) -> dict[str, Any]:
        profile, user = self._load_accessible(actor, user_id)
        record = self._record_for(actor, user_id)
        detail: dict[str, Any] = {
            "profile": IntakeService.mask_profile(profile),
            "user": {
                "id": user.id,
                "display_name": user.display_name,
                "role": user.role.value,
                "binding_status": user.binding_status.value,
                "line_user_id": user.line_user_id,
            },
            "record": record,
            "submissions": self._patients.list_submissions(user_id),
            "other_hospital_records": (
                self._records.list_for_user(user_id) if actor.role is Role.SUPER_ADMIN else []
            ),
        }
        for collection in CHILD_MODELS:
            detail[collection] = (
                self._records.list_children(collection, record["id"]) if record else []
            )
        return detail

    def list_audit(self, actor: Principal, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._audit.list(hospital_id=self._scope(actor), limit=limit)

    def dashboard(self, actor: Principal) -> dict[str, int]:
        scope = self._scope(actor)
        pending = self._patients.search(
            PatientSearch(hospital_id=scope, binding_statuses=(BindingStatus.PENDING,), limit=500)
        )
        return {
            "pending_patients": len(pending),
            "due_follow_ups": self._records.count_due_follow_ups(scope, today_tw()),
        }

    def verify(
        self,
        actor: Principal,
        user_id: str,
        *,
        hospital_id: str | None = None,
        medical_record_no: str | None = None,
        identity_method: str = "in_person",
        note: str | None = None,
    ) -> dict[str, Any]:
        profile, user = self._load_accessible(actor, user_id)
        hospital = self._resolve_hospital(actor, hospital_id)
        _require_choice(identity_method, IDENTITY_METHODS, "identity_method")
        medical_record_no = self._text(medical_record_no, 32)
        note = self._text(note, 500)  # 修改：note 不是字串時資料庫 adapter 會失敗

        record = self._records.get(user_id, hospital)
        if record is not None:
            # 已建檔：只是更新病歷號，不重跑核對、不再推播
            if medical_record_no:
                record = self._records.update(record["id"], medical_record_no=medical_record_no)
            return record

        if user.binding_status is BindingStatus.LOCKED:
            raise ConflictError("帳號已鎖定，請先解除鎖定再核對")
        latest = self._patients.latest_submission(user_id)
        if not profile.get("submitted_at") or latest is None or latest["status"] != "submitted":
            raise ValidationError("病人尚未送出（或已被退回）表單，無法核對")
        if profile.get("preferred_hospital_id") != hospital or not profile.get(
            "hospital_confirmed_at"
        ):
            raise ConflictError(
                "病人尚未確認在本院就醫。請先按「請病人確認」，病人在 LINE 上確認後才能核對建檔",
                details={"hospital_confirmation_required": True},
            )

        record = self._records.create(
            user_id=user_id,
            hospital_id=hospital,
            medical_record_no=medical_record_no,
            identity_method=identity_method,
            identity_verified_by=actor.user_id,
            created_by=actor.user_id,
        )
        self._patients.review_latest_submission(
            user_id, status="reviewed", reviewed_by=actor.user_id or "", note=note
        )
        changes: dict[str, Any] = {"binding_status": BindingStatus.VERIFIED}
        if user.role.is_end_user:  # 不把院方人員降級成病人
            changes["role"] = (
                Role.FAMILY if profile.get("filler_relation") == "family" else Role.PATIENT
            )
        self._users.update(user_id, **changes)
        self._notify(user, "您的基本資料已由院所核對完成，現在可以使用個人化衛教與服藥提醒功能。")
        return record

    def reject(self, actor: Principal, user_id: str, *, reason: Any) -> dict[str, Any] | None:
        _, user = self._load_accessible(actor, user_id)
        reason = self._text(reason, 500)  # 修改：reason 可能不是字串
        if not reason:
            raise ValidationError("請填寫退回原因")
        if user.binding_status is not BindingStatus.PENDING:
            raise ConflictError("只有待核對的病人可以退回補正")
        result = self._patients.review_latest_submission(
            user_id, status="rejected", reviewed_by=actor.user_id or "", note=reason
        )
        self._patients.clear_submitted(user_id)
        self._users.update(user_id, binding_status=BindingStatus.UNBOUND)
        self._notify(
            user, f"您的基本資料需要補正：{reason.strip()}\n請再次開啟設定頁修改後重新送出。"
        )
        return result

    def find_unclaimed(
        self,
        actor: Principal,
        *,
        phone: str | None = None,
        name: str | None = None,
        last4: str | None = None,
    ) -> list[dict[str, Any]]:
        """「新增病人」的第一步：用手機或「姓名 + 身分證末四碼」精準找出還沒被任何院所新增的病人。

        不做模糊搜尋，避免院所瀏覽不屬於自己的病人資料；找到後由 `request_hospital_confirmation()`
        把病人加進本院並推播請病人確認。
        """
        self._scope(actor)  # 院方帳號必須有所屬院所（SUPER_ADMIN 不限）
        phone = self._text(phone, 20)
        name = self._text(name, 50)
        last4 = self._text(last4, 4)
        if not phone and not (name and last4):
            raise ValidationError("請輸入手機號碼，或姓名加身分證末四碼")
        return self._patients.find_unclaimed(phone=phone, name=name, last4=last4)

    def request_hospital_confirmation(
        self, actor: Principal, user_id: str, *, hospital_id: str | None = None
    ) -> dict[str, Any]:
        """把病人加進本院（`preferred_hospital_id` = 本院、尚未確認）並推播 LINE 請病人自己按
        「是，我在這家院所就醫」；病人確認後本院才能核對建檔。已由其他院所新增的病人不能碰。"""
        hospital = self._resolve_hospital(actor, hospital_id)
        profile = self._patients.get_profile(user_id)
        user = self._users.get_by_id(user_id)
        if profile is None or user is None:
            raise NotFoundError("找不到病人資料")
        if not profile.get("submitted_at"):
            raise ValidationError("病人尚未送出表單")
        if user.binding_status is BindingStatus.LOCKED:
            raise ConflictError("帳號已鎖定，請先解除鎖定")
        if self._records.get(user_id, hospital) is not None:
            raise ConflictError("病人已在本院建檔，不需要再確認")
        current = profile.get("preferred_hospital_id")
        if current and current != hospital:
            raise ConflictError("這位病人已由其他院所新增，處理中")
        if current == hospital and profile.get("hospital_confirmed_at"):
            raise ConflictError("病人已確認在本院就醫，可以直接核對建檔")
        if not user.line_user_id:
            raise ValidationError("這位使用者沒有 LINE 帳號，無法推播")
        if current != hospital:
            self._patients.set_hospital(user_id, hospital, confirmed=False)
        name = self._hospitals.get(hospital)["name"]
        try:
            self._line().push(user.line_user_id, common.hospital_confirm_messages(name, hospital))
        except Exception as exc:  # noqa: BLE001 - 推播失敗要讓院方知道，而不是默默成功
            logger.warning("Hospital confirmation push failed for %s: %s", user_id, exc)
            raise ExternalServiceError("LINE 推播失敗，請稍後再試") from exc
        return {"requested": True, "hospital_id": hospital, "hospital_name": name}

    def set_locked(
        self, actor: Principal, user_id: str, *, locked: bool, reason: str | None = None
    ) -> dict[str, str]:
        """鎖定 / 解除鎖定。解鎖後回到 UNBOUND，病人可重新填寫。"""
        _, user = self._load_accessible(actor, user_id)
        if locked and user.binding_status is BindingStatus.LOCKED:
            raise ConflictError("帳號已是鎖定狀態")
        if not locked and user.binding_status is not BindingStatus.LOCKED:
            raise ConflictError("帳號並未鎖定")
        new_status = BindingStatus.LOCKED if locked else BindingStatus.UNBOUND
        self._users.update(user_id, binding_status=new_status)
        self._audit.record(
            actor_id=actor.user_id or "",
            action="patient.lock" if locked else "patient.unlock",
            target_user_id=user_id,
            hospital_id=actor.hospital_id,
            detail={"reason": reason} if reason else None,
        )
        self._notify(
            user,
            "您的帳號已由院所鎖定，請於就診時向院所人員說明。"
            if locked
            else "您的帳號已解除鎖定，可以重新填寫基本資料。",
        )
        return {"binding_status": new_status.value}

    def correct_profile(
        self, actor: Principal, user_id: str, changes: dict[str, Any], *, note: str | None = None
    ) -> dict[str, Any]:
        profile, _ = self._load_accessible(actor, user_id)
        record = self._require_record(actor, user_id)
        note = self._text(note, 500)  # 修改
        unknown = set(changes) - set(ALL_FIELDS)
        blocked = set(changes) & NON_CORRECTABLE
        if unknown or blocked:
            raise ValidationError(
                "有不允許修正的欄位",
                details={"unknown": sorted(unknown), "blocked": sorted(blocked)},
            )
        corrections: dict[str, dict[str, Any]] = {}
        for step in steps_for_keys(set(changes)):
            step_keys = {f.key for f in step.fields} & set(changes)
            merged = {**profile, **{k: changes[k] for k in step_keys}}
            cleaned = validate_step(
                step, merged, partial=True, skip_required=frozenset({"national_id"})
            )
            subset = {k: cleaned[k] for k in step_keys}
            self._patients.save_step(user_id, step, subset)
            for k in step_keys:
                corrections[k] = {"from": profile.get(k), "to": cleaned[k]}
        self._records.add_child(
            "reviews",
            record["id"],
            reviewed_by=actor.user_id,
            review_note=note,
            corrections=corrections,
        )
        return IntakeService.mask_profile(self._patients.get_profile(user_id) or {})

    def add_encounter(self, actor: Principal, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        values = self._encounter_values(body, creating=True)
        return self._records.add_child(
            "encounters", record["id"], created_by=actor.user_id, **values
        )

    def update_encounter(
        self, actor: Principal, user_id: str, encounter_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        values = self._encounter_values(body, creating=False)
        return self._update_child("encounters", record["id"], encounter_id, values)

    def add_flag(self, actor: Principal, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        return self._records.add_child(
            "flags",
            record["id"],
            flag_type=_require_choice(body.get("flag_type"), FLAG_TYPES, "flag_type"),
            level=_require_choice(body.get("level", "medium"), FLAG_LEVELS, "level"),
            note=self._text(body.get("note"), 500),
            created_by=actor.user_id,
        )

    def update_flag(
        self, actor: Principal, user_id: str, flag_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        values: dict[str, Any] = {}
        if "level" in body:
            values["level"] = _require_choice(body["level"], FLAG_LEVELS, "level")
        if "note" in body:
            values["note"] = self._text(body["note"], 500)
        if "active" in body:
            values["active"] = bool(body["active"])
        return self._update_child("flags", record["id"], flag_id, values)

    def add_follow_up(self, actor: Principal, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        return self._records.add_child(
            "follow_ups",
            record["id"],
            due_date=self._date(body.get("due_date"), "due_date", required=True),
            kind=_require_choice(body.get("kind"), FOLLOW_UP_KINDS, "kind"),
            description=self._text(body.get("description"), 500),
            assignee_id=self._text(body.get("assignee_id"), 32),
            created_by=actor.user_id,
        )

    def update_follow_up(
        self, actor: Principal, user_id: str, follow_up_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        values: dict[str, Any] = {}
        if "status" in body:
            values["status"] = _require_choice(body["status"], FOLLOW_UP_STATUSES, "status")
        if "due_date" in body:
            values["due_date"] = self._date(body["due_date"], "due_date", required=True)
        if "description" in body:
            values["description"] = self._text(body["description"], 500)
        return self._update_child("follow_ups", record["id"], follow_up_id, values)

    def add_note(self, actor: Principal, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        record = self._require_record(actor, user_id)
        text = self._text(body.get("body"), 2000)
        if not text:
            raise ValidationError("備註內容不可空白")
        return self._records.add_child(
            "notes",
            record["id"],
            body=text,
            visibility=_require_choice(
                body.get("visibility", "staff_only"), NOTE_VISIBILITY, "visibility"
            ),
            author_id=actor.user_id,
        )

    @staticmethod
    def _scope(actor: Principal) -> str | None:
        return AccessPolicy.hospital_scope(actor)

    def _resolve_hospital(self, actor: Principal, hospital_id: str | None) -> str:
        target = hospital_id or actor.hospital_id
        if not target:
            raise ValidationError("請指定 hospital_id")
        if not AccessPolicy.can_access_hospital(actor, target):
            raise AuthorizationError("不得操作其他院所的病人資料")
        self._hospitals.require_active(target)
        return target

    def _load_accessible(self, actor: Principal, user_id: str) -> tuple[dict[str, Any], UserRecord]:
        profile = self._patients.get_profile(user_id)
        user = self._users.get_by_id(user_id)
        if profile is None or user is None:
            raise NotFoundError("找不到病人資料")
        if actor.role is not Role.SUPER_ADMIN:
            scope = self._scope(actor)
            # 尚未建檔的病人：只有本院「新增」過（preferred_hospital_id 是本院）且已送出或被鎖定
            # 才對院所可見；別家新增的、還沒人新增的都看不到（SUPER_ADMIN 不受限）
            submitted_or_locked = bool(profile.get("submitted_at")) or (
                user.binding_status is BindingStatus.LOCKED
            )
            visible = (
                profile.get("preferred_hospital_id") == scope and submitted_or_locked
            ) or self._records.get(user_id, scope) is not None
            if not visible:
                raise AuthorizationError("這位病人不在你的院所範圍內")
        return profile, user

    def _record_for(self, actor: Principal, user_id: str) -> dict[str, Any] | None:
        if actor.hospital_id:
            return self._records.get(user_id, actor.hospital_id)
        if actor.role is Role.SUPER_ADMIN:
            records = self._records.list_for_user(user_id)
            if len(records) > 1:
                raise ValidationError("此病人在多家院所建檔，請以該院所的帳號操作")
            return records[0] if records else None
        return None

    def _require_record(self, actor: Principal, user_id: str) -> dict[str, Any]:
        self._load_accessible(actor, user_id)
        record = self._record_for(actor, user_id)
        if record is None:
            raise ConflictError("病人尚未在本院建檔，請先完成身份核對")
        if not AccessPolicy.can_access_hospital(actor, record["hospital_id"]):
            raise AuthorizationError("不得操作其他院所的病人資料")
        return record

    def _update_child(
        self, collection: str, record_id: str, child_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        if not values:
            raise ValidationError("沒有可更新的欄位")
        updated = self._records.update_child(collection, record_id, child_id, **values)
        if updated is None:
            raise NotFoundError("找不到要更新的資料")
        return updated

    def _encounter_values(self, body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if creating or "encounter_type" in body:
            values["encounter_type"] = _require_choice(
                body.get("encounter_type"), ENCOUNTER_TYPES, "encounter_type"
            )
        if creating or "visit_date" in body:
            values["visit_date"] = self._date(body.get("visit_date"), "visit_date", required=True)
        for key, limit in (
            ("department", 50),
            ("attending_physician", 50),
            ("discharge_code", 32),
            ("patient_visible_summary", 5000),
        ):
            if key in body:
                values[key] = self._text(body[key], limit)
        for key in ("admitted_at", "discharged_at"):
            if key in body:
                values[key] = self._date(body[key], key, required=False)
        if "diagnoses" in body:
            diagnoses = body["diagnoses"] or []
            if not isinstance(diagnoses, list) or any(not isinstance(d, dict) for d in diagnoses):
                raise ValidationError("diagnoses 必須是 [{icd10_code, description}] 清單")
            values["diagnoses"] = [
                {
                    "icd10_code": self._text(d.get("icd10_code"), 16),
                    "description": self._text(d.get("description"), 200),
                }
                for d in diagnoses
            ]
        return values

    @staticmethod
    def _text(value: Any, max_length: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) > max_length:
            raise ValidationError(f"文字長度不可超過 {max_length} 字")
        return text or None

    @staticmethod
    def _date(value: Any, name: str, *, required: bool) -> str | None:
        if not value:
            if required:
                raise ValidationError(f"{name} 為必填")
            return None
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise ValidationError(f"{name} 日期格式須為 YYYY-MM-DD") from exc

    def _notify(self, user: UserRecord, text: str) -> None:
        """推播失敗只記 log，狀態已寫入 DB，不能讓請求變 500。"""
        if not user.line_user_id:
            return
        try:
            self._line().push(user.line_user_id, [M.text(text)])
        except Exception:
            logger.exception("LINE push failed for user %s", user.id)
