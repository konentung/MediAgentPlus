"""院所模型。`id` 就是院所代碼（例如 `H1`、`NTUH`），其他表的 `hospital_id` 都存這個值。

`users` / `handoff_tickets` / `patient_profiles.preferred_hospital_id` / `audit_logs`
以 SET NULL 外鍵指向這裡；`patient_records.hospital_id` 為 RESTRICT（有病歷的院所不能刪）。
應用層另以 `HospitalService.require_active` 確認院所啟用中。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


class HospitalModel(db.Model, TimestampMixin):
    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 院所代碼，人可讀
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(50))  # 縣市 / 區域，供急診分流用
    address: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    # 醫學中心 / 區域醫院 / 地區醫院 / 診所（Roadmap 第 1 項會用到，先保留欄位）
    capacity_tier: Mapped[str | None] = mapped_column(String(16))
    # 座標（找醫院 / 分流建議依距離排序用）；來源：開放資料或後台手填，沒有也能運作（退回縣市比對）
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32))
