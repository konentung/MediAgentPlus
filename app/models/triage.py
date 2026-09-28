"""急診分流（找醫院）的資料模型。

- `er_snapshots`：健保署急診即時訊息的時序快照，(醫院, 回報時間) 唯一，cron 重跑不會重複。
  只用來依使用者位置推薦急診醫院，沒有分流建議 / 接收者（2026-09-28 移除）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin


def _new_id() -> str:
    return uuid.uuid4().hex


class ErSnapshotModel(db.Model, TimestampMixin):
    __tablename__ = "er_snapshots"
    __table_args__ = (
        UniqueConstraint("nhi_hospital_id", "reported_at", name="uq_er_snapshot_slot"),
        Index("ix_er_snapshots_area_reported", "area_code", "reported_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    # 醫事機構代碼（與 hospitals.id 相同時可帶出地址 / 電話）
    nhi_hospital_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    hospital_name: Mapped[str] = mapped_column(String(100), nullable=False)
    area_code: Mapped[str] = mapped_column(String(4), nullable=False)  # 健保署地區代碼
    hosp_type: Mapped[str] = mapped_column(String(2), nullable=False)  # 1 醫中 / 2 區域 / 3 地區
    full_119: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wait_see: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wait_bed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 不可靠，僅保留
    wait_admit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wait_icu: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reported_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
