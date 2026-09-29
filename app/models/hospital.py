"""院所模型。`id` 就是院所代碼（例如 `H1`、`NTUH`），其他表的 `hospital_id` 都存這個值。

`users` / `feedback` / `patient_profiles.preferred_hospital_id` / `audit_logs`
以 SET NULL 外鍵指向這裡；`patient_records.hospital_id` 為 RESTRICT（有病歷的院所不能刪）。
應用層另以 `HospitalService.require_active` 確認院所啟用中。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


class HospitalModel(db.Model, TimestampMixin):
    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 院所代碼，人可讀
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(50))  # 縣市（臺 → 台），找附近診所依此篩選
    address: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    # medical_center / regional / district / clinic；「非急症找附近診所」只取後三種
    capacity_tier: Mapped[str | None] = mapped_column(String(16))
    # 診療科別，逗號分隔（衛福部「醫療機構與人員基本資料」的「科別」用詞，例如 家庭醫學科,內科）。
    # 找附近診所依 AI 建議的科別篩選（`triage_rules.department_terms()` 對應同義詞）
    departments: Mapped[str | None] = mapped_column(Text)
    # 座標：找醫院依距離推薦與找附近診所用；來源 data/er_hospitals.csv、匯入 CSV 或後台手填。
    # 沒有座標的院所不會出現在依距離的推薦裡
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32))
