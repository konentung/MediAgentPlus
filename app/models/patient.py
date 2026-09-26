"""病人自填資料（Intake）模型：跟著 `users` 走，跨院所共用一份。

對應設計文件 docs/patient-form-design.md §4.1。
純量欄位攤平在 `patient_profiles`；過敏、病史、用藥、緊急聯絡人拆表方便院方跨病人查詢；
`patient_form_submissions` 保存每次送出的原始 JSON 快照供稽核。
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin

# PostgreSQL 用 JSONB，測試環境的 SQLite 退回一般 JSON
JsonColumn = JSONB().with_variant(JSON(), "sqlite")


def _new_id() -> str:
    return uuid.uuid4().hex


def _user_fk(**kwargs) -> Mapped[str]:
    return mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, **kwargs
    )


class PatientProfileModel(db.Model, TimestampMixin):
    __tablename__ = "patient_profiles"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    filler_relation: Mapped[str | None] = mapped_column(String(16))  # self / family
    filler_name: Mapped[str | None] = mapped_column(String(50))
    filler_relationship: Mapped[str | None] = mapped_column(String(16))
    filler_phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str | None] = mapped_column(String(50))
    national_id_encrypted: Mapped[str | None] = mapped_column(String(255))
    national_id_last4: Mapped[str | None] = mapped_column(String(4))
    national_id_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    birth_date: Mapped[str | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(8))
    preferred_language: Mapped[str | None] = mapped_column(String(16))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    address_city: Mapped[str | None] = mapped_column(String(20))
    address_district: Mapped[str | None] = mapped_column(String(20))
    address_street: Mapped[str | None] = mapped_column(String(200))
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1))
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1))
    blood_type: Mapped[str | None] = mapped_column(String(8))
    rh: Mapped[str | None] = mapped_column(String(8))
    pregnancy_status: Mapped[str | None] = mapped_column(String(16))
    smoking: Mapped[str | None] = mapped_column(String(8))
    smoking_per_day: Mapped[int | None] = mapped_column(Integer)
    drinking: Mapped[str | None] = mapped_column(String(16))
    betel_nut: Mapped[str | None] = mapped_column(String(8))
    drug_allergies_none: Mapped[bool | None] = mapped_column(Boolean)
    food_allergies_none: Mapped[bool | None] = mapped_column(Boolean)
    chronic_conditions_none: Mapped[bool | None] = mapped_column(Boolean)
    other_allergies: Mapped[list | None] = mapped_column(JsonColumn)
    condition_notes: Mapped[str | None] = mapped_column(String(500))
    has_current_medications: Mapped[str | None] = mapped_column(String(8))
    supplements: Mapped[str | None] = mapped_column(String(300))
    preferred_hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    nhi_status: Mapped[str | None] = mapped_column(String(16))
    catastrophic_illness_card: Mapped[bool | None] = mapped_column(Boolean)
    disability_card: Mapped[bool | None] = mapped_column(Boolean)
    accessibility_needs: Mapped[list | None] = mapped_column(JsonColumn)
    completed_steps: Mapped[list | None] = mapped_column(JsonColumn, default=list)
    submitted_at: Mapped[str | None] = mapped_column(db.DateTime(timezone=True))


class PatientEmergencyContactModel(db.Model, TimestampMixin):
    __tablename__ = "patient_emergency_contacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(16))
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PatientAllergyModel(db.Model, TimestampMixin):
    __tablename__ = "patient_allergies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # drug / food
    substance: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reaction: Mapped[str | None] = mapped_column(String(16))
    severity: Mapped[str | None] = mapped_column(String(8))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PatientConditionModel(db.Model, TimestampMixin):
    __tablename__ = "patient_conditions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # chronic / surgery / family
    code: Mapped[str | None] = mapped_column(String(32), index=True)  # 多選選項值
    description: Mapped[str | None] = mapped_column(String(200))
    year: Mapped[int | None] = mapped_column(Integer)
    hospital_name: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PatientMedicationModel(db.Model, TimestampMixin):
    __tablename__ = "patient_medications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    drug_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    dose: Mapped[str | None] = mapped_column(String(50))
    frequency: Mapped[str | None] = mapped_column(String(8))
    purpose: Mapped[str | None] = mapped_column(String(100))
    # self_reported：病人自填；prescription：之後由處方（Roadmap 第 4 項）同步
    source: Mapped[str] = mapped_column(String(16), default="self_reported", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PatientConsentModel(db.Model, TimestampMixin):
    """同意紀錄：每次變更新增一列、不覆寫，方便舉證。"""

    __tablename__ = "patient_consents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    consent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_version: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    line_user_id: Mapped[str | None] = mapped_column(String(64))


class PatientFormSubmissionModel(db.Model, TimestampMixin):
    """每次送出的原始快照。院方審核只改結構化表，不改這裡。"""

    __tablename__ = "patient_form_submissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = _user_fk(index=True)
    form_type: Mapped[str] = mapped_column(String(16), default="intake", nullable=False)
    answers: Mapped[dict] = mapped_column(JsonColumn, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="submitted", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(32))
    reviewed_at: Mapped[str | None] = mapped_column(db.DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "JsonColumn",
    "PatientProfileModel",
    "PatientEmergencyContactModel",
    "PatientAllergyModel",
    "PatientConditionModel",
    "PatientMedicationModel",
    "PatientConsentModel",
    "PatientFormSubmissionModel",
]
