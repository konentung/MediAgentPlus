"""院方病人紀錄模型：跟著院所走，院所之間互相看不到。

對應設計文件 docs/patient-form-design.md §3、§4.2。
`patient_records` 是「某院所為某病人建檔」的那一筆，其餘表都掛在它底下（CASCADE）。
`hospital_id` 以 RESTRICT 外鍵指向 `hospitals`：有病歷的院所不能刪。
建檔的前提是病人已在 LINE 上確認在該院就醫（`patient_profiles.hospital_confirmed_at`）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin
from app.models.patient import JsonColumn


def _new_id() -> str:
    return uuid.uuid4().hex


def _record_fk() -> Mapped[str]:
    return mapped_column(
        String(32),
        ForeignKey("patient_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class PatientRecordModel(db.Model, TimestampMixin):
    __tablename__ = "patient_records"
    __table_args__ = (UniqueConstraint("user_id", "hospital_id", name="uq_patient_record_scope"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hospital_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    medical_record_no: Mapped[str | None] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16), default="liff_form", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    identity_method: Mapped[str | None] = mapped_column(String(16))  # nhi_card / id_card / ...
    identity_verified_at: Mapped[str | None] = mapped_column(db.DateTime(timezone=True))
    identity_verified_by: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(32))


class PatientRecordReviewModel(db.Model, TimestampMixin):
    """院方對自填資料的審核 / 修正紀錄（結構化表改了什麼，原始送出資料不動）。"""

    __tablename__ = "patient_record_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    record_id: Mapped[str] = _record_fk()
    reviewed_by: Mapped[str] = mapped_column(String(32), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    corrections: Mapped[dict | None] = mapped_column(JsonColumn)  # {欄位: {"from":..,"to":..}}


class PatientEncounterModel(db.Model, TimestampMixin):
    __tablename__ = "patient_encounters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    record_id: Mapped[str] = _record_fk()
    encounter_type: Mapped[str] = mapped_column(String(16), nullable=False)  # opd / er / inpatient
    visit_date: Mapped[str] = mapped_column(Date, nullable=False)
    department: Mapped[str | None] = mapped_column(String(50))
    attending_physician: Mapped[str | None] = mapped_column(String(50))
    diagnoses: Mapped[list | None] = mapped_column(JsonColumn)  # [{"icd10_code", "description"}]
    admitted_at: Mapped[str | None] = mapped_column(Date)
    discharged_at: Mapped[str | None] = mapped_column(Date)
    discharge_code: Mapped[str | None] = mapped_column(String(32), index=True)
    patient_visible_summary: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(32))


class PatientRiskFlagModel(db.Model, TimestampMixin):
    __tablename__ = "patient_risk_flags"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    record_id: Mapped[str] = _record_fk()
    flag_type: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), default="medium", nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32))


class PatientFollowUpModel(db.Model, TimestampMixin):
    __tablename__ = "patient_follow_ups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    record_id: Mapped[str] = _record_fk()
    due_date: Mapped[str] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(32))


class PatientRecordNoteModel(db.Model, TimestampMixin):
    __tablename__ = "patient_record_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    record_id: Mapped[str] = _record_fk()
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="staff_only", nullable=False)
    author_id: Mapped[str] = mapped_column(String(32), nullable=False)


__all__ = [
    "PatientRecordModel",
    "PatientRecordReviewModel",
    "PatientEncounterModel",
    "PatientRiskFlagModel",
    "PatientFollowUpModel",
    "PatientRecordNoteModel",
]
