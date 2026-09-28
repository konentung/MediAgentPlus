"""SQLAlchemy 模型層。

每個模型繼承 `db.Model, TimestampMixin`；模型需在此 import，Flask-Migrate 才能偵測到。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


from app.models.audit import AuditLogModel  # noqa: E402
from app.models.feedback import FeedbackModel  # noqa: E402
from app.models.hospital import HospitalModel  # noqa: E402
from app.models.knowledge import (  # noqa: E402
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeJobModel,
)
from app.models.medication import (  # noqa: E402
    DrugModel,
    MedicationScheduleModel,
    PharmacyModel,
    PrescriptionModel,
    ReminderDeliveryModel,
)
from app.models.patient import (  # noqa: E402
    PatientAllergyModel,
    PatientConditionModel,
    PatientConsentModel,
    PatientEmergencyContactModel,
    PatientFormSubmissionModel,
    PatientMedicationModel,
    PatientProfileModel,
)
from app.models.patient_record import (  # noqa: E402
    PatientEncounterModel,
    PatientFollowUpModel,
    PatientRecordModel,
    PatientRecordNoteModel,
    PatientRecordReviewModel,
    PatientRiskFlagModel,
)
from app.models.triage import ErSnapshotModel  # noqa: E402
from app.models.user import UserModel  # noqa: E402

__all__ = [
    "TimestampMixin",
    "AuditLogModel",
    "UserModel",
    "FeedbackModel",
    "HospitalModel",
    "PrescriptionModel",
    "PharmacyModel",
    "DrugModel",
    "MedicationScheduleModel",
    "ReminderDeliveryModel",
    "PatientProfileModel",
    "PatientEmergencyContactModel",
    "PatientAllergyModel",
    "PatientConditionModel",
    "PatientMedicationModel",
    "PatientConsentModel",
    "PatientFormSubmissionModel",
    "PatientRecordModel",
    "PatientRecordReviewModel",
    "PatientEncounterModel",
    "PatientRiskFlagModel",
    "PatientFollowUpModel",
    "PatientRecordNoteModel",
    "KnowledgeDocumentModel",
    "KnowledgeChunkModel",
    "KnowledgeJobModel",
    "ErSnapshotModel",
]
