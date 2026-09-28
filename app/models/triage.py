"""急診動態分流（Roadmap 第 1 項）的資料模型。

- `er_snapshots`：健保署急診即時訊息的時序快照，(醫院, 回報時間) 唯一，cron 重跑不會重複。
- `triage_advisories`：某縣市出現壅塞醫院時開立的「分流建議」，解除後保留成歷史。
- `triage_subscriptions`：分流建議的接收者（LINE 推播 / Webhook / API 拉取），由院方後台建立，
  身份由建立者線下核對；`hospital_id` 決定哪個院所的管理員看得到這筆訂閱。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models import TimestampMixin

JsonColumn = JSONB().with_variant(JSON(), "sqlite")


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


class TriageAdvisoryModel(db.Model, TimestampMixin):
    __tablename__ = "triage_advisories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    area_code: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)  # open/resolved
    congested: Mapped[list] = mapped_column(JsonColumn, default=list, nullable=False)
    alternatives: Mapped[list] = mapped_column(JsonColumn, default=list, nullable=False)
    report: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snapshot_reported_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    notify_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TriageSubscriptionModel(db.Model, TimestampMixin):
    __tablename__ = "triage_subscriptions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # line / webhook / api
    label: Mapped[str] = mapped_column(String(100), nullable=False)  # 例：台北市消防局勤指中心
    area_codes: Mapped[list] = mapped_column(JsonColumn, default=list, nullable=False)  # 空 = 全國
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)  # webhook 簽章金鑰 / API key
    secret_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    hospital_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("hospitals.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_notified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    notify_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
