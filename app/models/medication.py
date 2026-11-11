"""藥物諮詢 / 服藥小幫手的資料模型。

處方的藥品清單存成 JSON 欄位、照片路徑直接掛在處方上；藥局 / 藥品是開放資料匯入的參考資料
（藥局可由自己的人員在後台修正電話 / 地址 / 營業時段）；提醒排程與推播紀錄各一張表；
`prescription_pickups` 是病人把處方指定到某家藥局領藥的需求。
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin

JsonColumn = JSONB().with_variant(JSON(), "sqlite")


def _new_id() -> str:
    return uuid.uuid4().hex


class PrescriptionModel(db.Model, TimestampMixin):
    """一張處方箋。`items` 是藥品清單 JSON，每項的鍵見 `PrescriptionService._item_from_extraction()`
    （drug_name / drug_name_raw / dose_text / frequency_code / timing / duration_days / quantity /
    instructions / nhi_code / category / confidence），修改時由 `validate_items()` 驗證。
    """

    __tablename__ = "prescriptions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="draft", nullable=False
    )  # draft / confirmed / discarded
    source: Mapped[str] = mapped_column(String(16), default="ocr", nullable=False)  # ocr / manual
    hospital_name: Mapped[str | None] = mapped_column(String(100))
    issued_on: Mapped[object | None] = mapped_column(Date)
    days_supply: Mapped[int | None] = mapped_column(Integer)
    is_chronic_refill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    refill_seq: Mapped[int | None] = mapped_column(Integer)
    refill_total: Mapped[int | None] = mapped_column(Integer)
    refill_reminder_on: Mapped[object | None] = mapped_column(Date)  # 慢箋領藥提醒日
    refill_reminded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list] = mapped_column(JsonColumn, default=list, nullable=False)
    warnings: Mapped[list | None] = mapped_column(JsonColumn)
    image_path: Mapped[str | None] = mapped_column(String(255))  # 加密後的照片檔路徑
    image_content_type: Mapped[str | None] = mapped_column(String(64))
    line_message_id: Mapped[str | None] = mapped_column(String(64))
    ocr_model: Mapped[str | None] = mapped_column(String(64))
    ocr_raw: Mapped[dict | None] = mapped_column(JsonColumn)
    confirmed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class PharmacyModel(db.Model, TimestampMixin):
    """藥局主檔（健保署特約醫事機構 + 衛福部藥局基本資料）。沒有經緯度，用縣市 / 鄉鎮市區配對。"""

    __tablename__ = "pharmacies"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # 醫事機構代碼或衍生代碼
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(10), index=True)  # 正規化：臺 → 台
    district: Mapped[str | None] = mapped_column(String(10), index=True)
    nhi_contracted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    opening_hours: Mapped[dict | None] = mapped_column(JsonColumn)  # {"mon": ["am","pm","night"]}
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 藥局自己（或系統管理員）在後台改過資料的時間；有值時 CSV 重新匯入不覆蓋電話 / 地址 / 營業時段
    self_managed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class PrescriptionPickupModel(db.Model, TimestampMixin):
    """病人把一張已確認的處方指定到某家藥局領藥。

    一張處方同一時間只指定給一家藥局（`prescription_id` 唯一）：
    改選別家 = 換藥局、狀態回到 requested。
    狀態：requested（病人送出）→ ready（藥局備好，推播通知病人）→ picked_up（已領）；
    cancelled = 藥局無法調劑（缺藥、管制藥…，原因寫在 `note`，一樣會推播）。
    """

    __tablename__ = "prescription_pickups"
    __table_args__ = (UniqueConstraint("prescription_id", name="uq_pickup_prescription"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    prescription_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pharmacy_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="requested", nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))  # 藥局給病人的說明
    handled_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL")
    )
    requested_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)  # UTC
    ready_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    picked_up_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class DrugModel(db.Model, TimestampMixin):
    """健保用藥品項（藥品代號為主鍵），供藥名正規化與 ATC 類別顯示。"""

    __tablename__ = "drugs"

    nhi_code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    name_zh: Mapped[str | None] = mapped_column(String(150), index=True)
    ingredient: Mapped[str | None] = mapped_column(String(300))
    dosage_form: Mapped[str | None] = mapped_column(String(50))
    atc_code: Mapped[str | None] = mapped_column(String(10), index=True)


class MedicationScheduleModel(db.Model, TimestampMixin):
    """一項藥的提醒排程；`times` 是台灣時間 HH:MM 清單。"""

    __tablename__ = "medication_schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prescription_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("prescriptions.id", ondelete="SET NULL")
    )
    drug_name: Mapped[str] = mapped_column(String(150), nullable=False)
    dose_text: Mapped[str | None] = mapped_column(String(50))
    frequency_code: Mapped[str] = mapped_column(String(8), nullable=False)
    timing: Mapped[str | None] = mapped_column(String(8))
    times: Mapped[list] = mapped_column(JsonColumn, default=list, nullable=False)
    start_date: Mapped[object] = mapped_column(Date, nullable=False)
    end_date: Mapped[object | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused_until: Mapped[object | None] = mapped_column(Date)


class ReminderDeliveryModel(db.Model, TimestampMixin):
    """每一次提醒推播；(schedule_id, scheduled_for) 唯一，cron 重跑不會重複送。"""

    __tablename__ = "reminder_deliveries"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_for", name="uq_delivery_slot"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    schedule_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("medication_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduled_for: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)  # UTC
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )  # pending / sent / taken / snoozed / skipped / failed
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    snooze_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
