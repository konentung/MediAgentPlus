"""病人自填資料 Repository：以「表單欄位 key → 值」的 dict 與 Service 層溝通。

欄位落到哪張表由 `app.domain.intake_schema.Field.storage` 決定，這裡負責 dict ↔ 多張表的轉換，
Service / API 層不需要知道底層拆成幾張表。只提供 SQLAlchemy 實作（沒有 in-memory 版本）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.domain.enums import BindingStatus
from app.domain.intake_schema import ALL_FIELDS, INTAKE_STEPS, Field, Step
from app.extensions import db
from app.models.hospital import HospitalModel
from app.models.patient import (
    PatientAllergyModel,
    PatientConditionModel,
    PatientConsentModel,
    PatientEmergencyContactModel,
    PatientFormSubmissionModel,
    PatientMedicationModel,
    PatientProfileModel,
)
from app.models.patient_record import PatientRecordModel
from app.models.user import UserModel
from app.repositories.base import row_to_dict

# profile 上不屬於表單欄位、但 Service 需要的中繼資料
PROFILE_META_KEYS = (
    "national_id_encrypted",
    "national_id_last4",
    "national_id_fingerprint",
    "completed_steps",
    "submitted_at",
    "created_at",
    "updated_at",
)


@dataclass
class NationalIdCipherText:
    encrypted: str
    last4: str
    fingerprint: str


@dataclass
class PatientSearch:
    """後台列表的查詢條件。"""

    hospital_id: str | None = None  # None = 不限制（SUPER_ADMIN）
    binding_statuses: tuple[BindingStatus, ...] = field(default_factory=tuple)
    query: str | None = None  # 姓名 / 手機 / 身分證末四碼 / 病歷號
    limit: int = 50


class PatientRepository(Protocol):
    def get_profile(self, user_id: str) -> dict[str, Any] | None: ...
    def save_step(
        self,
        user_id: str,
        step: Step,
        data: dict[str, Any],
        *,
        national_id: NationalIdCipherText | None = None,
    ) -> dict[str, Any]: ...
    def find_user_id_by_fingerprint(self, fingerprint: str) -> str | None: ...
    def add_submission(self, user_id: str, answers: dict[str, Any]) -> dict[str, Any]: ...
    def list_submissions(self, user_id: str) -> list[dict[str, Any]]: ...
    def review_latest_submission(
        self, user_id: str, *, status: str, reviewed_by: str, note: str | None
    ) -> dict[str, Any] | None: ...
    def latest_submission(self, user_id: str) -> dict[str, Any] | None: ...
    def clear_submitted(self, user_id: str) -> None: ...
    def add_consents(
        self,
        user_id: str,
        consents: dict[str, bool],
        *,
        version: str,
        ip_address: str | None,
        line_user_id: str | None,
    ) -> None: ...
    def latest_consents(self, user_id: str) -> dict[str, bool]: ...
    def search(self, criteria: PatientSearch) -> list[dict[str, Any]]: ...


_LIST_MODELS = {
    "contacts": PatientEmergencyContactModel,
    "allergy": PatientAllergyModel,
    "condition": PatientConditionModel,
    "medication": PatientMedicationModel,
}


def _storage_parts(f: Field) -> tuple[str, str | None]:
    kind, _, sub = f.storage.partition(":")
    return kind, (sub or None)


def _coerce_for_column(f: Field, value: Any) -> Any:
    if f.type == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


class SqlAlchemyPatientRepository:
    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        row = db.session.get(PatientProfileModel, user_id)
        if row is None:
            return None
        data = self._profile_columns(row)
        data.update(self._collect_lists(user_id))
        data.update(self.latest_consents(user_id))
        return data

    def _profile_columns(self, row: PatientProfileModel) -> dict[str, Any]:
        raw = row_to_dict(row)
        data = {k: raw.get(k) for k in PROFILE_META_KEYS}
        data["user_id"] = row.user_id
        for f in ALL_FIELDS.values():
            kind, _ = _storage_parts(f)
            if kind in ("profile", "json") and f.key in raw:
                data[f.key] = raw[f.key]
        return data

    def _collect_lists(self, user_id: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for f in ALL_FIELDS.values():
            kind, sub = _storage_parts(f)
            if kind not in _LIST_MODELS:
                continue
            model = _LIST_MODELS[kind]
            stmt = db.select(model).where(model.user_id == user_id).order_by(model.position)
            if kind == "allergy" or kind == "condition":
                stmt = stmt.where(model.kind == sub)
            if kind == "medication":
                stmt = stmt.where(model.source == "self_reported")
            rows = list(db.session.execute(stmt).scalars())
            if f.type == "multi":
                result[f.key] = [r.code for r in rows]
            else:
                keys = [sub_f.key for sub_f in f.item_fields]
                result[f.key] = [{k: getattr(r, k) for k in keys} for r in rows]
        return result

    def save_step(
        self,
        user_id: str,
        step: Step,
        data: dict[str, Any],
        *,
        national_id: NationalIdCipherText | None = None,
    ) -> dict[str, Any]:
        row = db.session.get(PatientProfileModel, user_id)
        if row is None:
            row = PatientProfileModel(user_id=user_id, completed_steps=[])
            db.session.add(row)

        for f in step.fields:
            if f.key not in data:
                continue
            kind, sub = _storage_parts(f)
            value = data[f.key]
            if f.key == "national_id":
                if national_id is not None:
                    row.national_id_encrypted = national_id.encrypted
                    row.national_id_last4 = national_id.last4
                    row.national_id_fingerprint = national_id.fingerprint
            elif kind in ("profile", "json"):
                setattr(row, f.key, _coerce_for_column(f, value))
            elif kind in _LIST_MODELS:
                self._replace_list(user_id, f, kind, sub, value or [])
            # storage=consent 由 Service 呼叫 add_consents 另外寫入

        completed = set(row.completed_steps or [])
        completed.add(step.number)
        row.completed_steps = sorted(completed)
        db.session.commit()
        return self.get_profile(user_id) or {}

    def _replace_list(
        self, user_id: str, f: Field, kind: str, sub: str | None, items: list[Any]
    ) -> None:
        model = _LIST_MODELS[kind]
        delete_stmt = db.delete(model).where(model.user_id == user_id)
        if kind in ("allergy", "condition"):
            delete_stmt = delete_stmt.where(model.kind == sub)
        if kind == "medication":
            delete_stmt = delete_stmt.where(model.source == "self_reported")
        db.session.execute(delete_stmt)

        for position, item in enumerate(items):
            if f.type == "multi":
                db.session.add(model(user_id=user_id, kind=sub, code=item, position=position))
                continue
            values = {k: v for k, v in item.items() if v is not None}
            if kind in ("allergy", "condition"):
                values["kind"] = sub
            if kind == "contacts":
                values["is_primary"] = position == 0
            db.session.add(model(user_id=user_id, position=position, **values))

    def find_user_id_by_fingerprint(self, fingerprint: str) -> str | None:
        return db.session.execute(
            db.select(PatientProfileModel.user_id).where(
                PatientProfileModel.national_id_fingerprint == fingerprint
            )
        ).scalar_one_or_none()

    def add_submission(self, user_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        row = PatientFormSubmissionModel(user_id=user_id, answers=answers)
        db.session.add(row)
        profile = db.session.get(PatientProfileModel, user_id)
        if profile is not None:
            profile.submitted_at = datetime.now(UTC)
        db.session.commit()
        return row_to_dict(row)

    def list_submissions(self, user_id: str) -> list[dict[str, Any]]:
        rows = db.session.execute(
            db.select(PatientFormSubmissionModel)
            .where(PatientFormSubmissionModel.user_id == user_id)
            .order_by(PatientFormSubmissionModel.created_at.desc())
        ).scalars()
        return [row_to_dict(r) for r in rows]

    def latest_submission(self, user_id: str) -> dict[str, Any] | None:
        row = db.session.execute(
            db.select(PatientFormSubmissionModel)
            .where(PatientFormSubmissionModel.user_id == user_id)
            .order_by(PatientFormSubmissionModel.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row_to_dict(row) if row else None

    def clear_submitted(self, user_id: str) -> None:
        """退回後清掉送出時間，病人重新送出前後台不能再核對。"""
        profile = db.session.get(PatientProfileModel, user_id)
        if profile is not None:
            profile.submitted_at = None
            db.session.commit()

    def review_latest_submission(
        self, user_id: str, *, status: str, reviewed_by: str, note: str | None
    ) -> dict[str, Any] | None:
        row = db.session.execute(
            db.select(PatientFormSubmissionModel)
            .where(PatientFormSubmissionModel.user_id == user_id)
            .order_by(PatientFormSubmissionModel.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = status
        row.reviewed_by = reviewed_by
        row.reviewed_at = datetime.now(UTC)
        row.review_note = note
        db.session.commit()
        return row_to_dict(row)

    def add_consents(
        self,
        user_id: str,
        consents: dict[str, bool],
        *,
        version: str,
        ip_address: str | None,
        line_user_id: str | None,
    ) -> None:
        for consent_type, granted in consents.items():
            db.session.add(
                PatientConsentModel(
                    user_id=user_id,
                    consent_type=consent_type,
                    granted=bool(granted),
                    consent_version=version,
                    ip_address=ip_address,
                    line_user_id=line_user_id,
                )
            )
        db.session.commit()

    def latest_consents(self, user_id: str) -> dict[str, bool]:
        rows = db.session.execute(
            db.select(PatientConsentModel)
            .where(PatientConsentModel.user_id == user_id)
            .order_by(PatientConsentModel.created_at.asc())
        ).scalars()
        latest: dict[str, bool] = {}
        for r in rows:  # 由舊到新覆寫，最後留下最新一筆
            latest[r.consent_type] = r.granted
        return latest

    def search(self, criteria: PatientSearch) -> list[dict[str, Any]]:
        stmt = (
            db.select(PatientProfileModel, UserModel, PatientRecordModel, HospitalModel.name)
            .join(UserModel, UserModel.id == PatientProfileModel.user_id)
            .outerjoin(HospitalModel, HospitalModel.id == PatientProfileModel.preferred_hospital_id)
            .outerjoin(
                PatientRecordModel,
                db.and_(
                    PatientRecordModel.user_id == PatientProfileModel.user_id,
                    (
                        PatientRecordModel.hospital_id == criteria.hospital_id
                        if criteria.hospital_id is not None
                        else db.true()
                    ),
                ),
            )
            # 未送出的草稿不列出，但被鎖定的帳號要讓院方看得到才能處理
            .where(
                db.or_(
                    PatientProfileModel.submitted_at.is_not(None),
                    UserModel.binding_status == BindingStatus.LOCKED,
                )
            )
            .order_by(PatientProfileModel.submitted_at.desc())
        )
        if criteria.hospital_id is not None:
            # 院所只看得到：指定本院為常去院所的待審核病人，或已在本院建檔的病人
            stmt = stmt.where(
                db.or_(
                    PatientProfileModel.preferred_hospital_id == criteria.hospital_id,
                    PatientRecordModel.id.is_not(None),
                )
            )
        if criteria.binding_statuses:
            stmt = stmt.where(UserModel.binding_status.in_(list(criteria.binding_statuses)))
        if criteria.query:
            q = criteria.query.strip()
            stmt = stmt.where(
                db.or_(
                    PatientProfileModel.full_name.ilike(f"%{q}%"),
                    PatientProfileModel.phone == q,
                    PatientProfileModel.national_id_last4 == q[-4:],
                    PatientRecordModel.medical_record_no == q,
                )
            )
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for profile, user, record, hospital_name in db.session.execute(stmt):
            # SUPER_ADMIN 不限院所時，同一病人在多家院所建檔會 join 出多列，只留第一列
            if profile.user_id in seen:
                continue
            seen.add(profile.user_id)
            if len(results) >= criteria.limit:
                break
            results.append(
                {
                    "user_id": profile.user_id,
                    "full_name": profile.full_name,
                    "phone": profile.phone,
                    "national_id_last4": profile.national_id_last4,
                    "birth_date": profile.birth_date.isoformat() if profile.birth_date else None,
                    "sex": profile.sex,
                    "filler_relation": profile.filler_relation,
                    "preferred_hospital_id": profile.preferred_hospital_id,
                    "preferred_hospital_name": hospital_name,
                    "submitted_at": profile.submitted_at.isoformat()
                    if profile.submitted_at
                    else None,
                    "binding_status": user.binding_status.value,
                    "role": user.role.value,
                    "display_name": user.display_name,
                    "record_id": record.id if record else None,
                    "medical_record_no": record.medical_record_no if record else None,
                }
            )
        return results


def steps_for_keys(keys: set[str]) -> list[Step]:
    """回傳包含任一指定欄位的步驟（供院方修正資料時決定要重存哪些步驟）。"""
    return [s for s in INTAKE_STEPS if any(f.key in keys for f in s.fields)]
