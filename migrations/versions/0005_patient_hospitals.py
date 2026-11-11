"""病人的就醫院所改成每家院所一筆（patient_hospitals），取代 patient_profiles 上的單一欄位。

原本病人身上只有一個 `preferred_hospital_id` + `hospital_confirmed_at`，第一家院所加入後
第二家就取不到病人資料；改成 `patient_hospitals`（user_id × hospital_id 唯一）後，
病人在 LIFF「我的就醫院所」可以加多家，每家院所各自請病人確認、各自建檔。
既有資料：有選院所的病人搬成一筆 `source=patient`，確認時間照搬。

Revision ID: 0005_patient_hospitals
Revises: 0004_pharmacy_staff_pickups
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0005_patient_hospitals"
down_revision = "0004_pharmacy_staff_pickups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_hospitals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("hospital_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "hospital_id", name="uq_patient_hospital"),
    )
    op.create_index("ix_patient_hospitals_user_id", "patient_hospitals", ["user_id"])
    op.create_index("ix_patient_hospitals_hospital_id", "patient_hospitals", ["hospital_id"])

    # 搬既有資料：病人選過的院所 → 一筆 source=patient；確認時間照搬
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT user_id, preferred_hospital_id, hospital_confirmed_at, updated_at "
            "FROM patient_profiles WHERE preferred_hospital_id IS NOT NULL"
        )
    ).fetchall()
    # 用純 SQL 逐筆搬（不經 SQLAlchemy 型別轉換），PostgreSQL 與 SQLite 讀回的時間值都能原樣寫入
    insert = sa.text(
        "INSERT INTO patient_hospitals "
        "(id, user_id, hospital_id, source, requested_at, confirmed_at, created_at, updated_at) "
        "VALUES (:id, :u, :h, 'patient', :t, :c, :t, :t)"
    )
    for user_id, hospital_id, confirmed_at, updated_at in rows:
        conn.execute(
            insert,
            {
                "id": uuid.uuid4().hex,
                "u": user_id,
                "h": hospital_id,
                "t": updated_at,
                "c": confirmed_at,
            },
        )

    # 外鍵沒有命名（0001 建表時未指定）；PostgreSQL 砍欄位會連同該欄位的外鍵一起移除，
    # SQLite 由 batch 模式重建整張表，所以兩邊都不必另外 drop_constraint
    with op.batch_alter_table("patient_profiles") as batch:
        batch.drop_index("ix_patient_profiles_preferred_hospital_id")
        batch.drop_column("hospital_confirmed_at")
        batch.drop_column("preferred_hospital_id")


def downgrade() -> None:
    with op.batch_alter_table("patient_profiles") as batch:
        batch.add_column(sa.Column("preferred_hospital_id", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("hospital_confirmed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_index("ix_patient_profiles_preferred_hospital_id", ["preferred_hospital_id"])
        # SQLite 的 batch 模式要求外鍵有名字（0001 建的那個沒有名字，降版後名稱會不同，不影響使用）
        batch.create_foreign_key(
            "fk_patient_profiles_preferred_hospital_id",
            "hospitals",
            ["preferred_hospital_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # 多家院所只能留一家：取最近確認（或最近加入）的那一筆
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT user_id, hospital_id, confirmed_at FROM patient_hospitals "
            "ORDER BY user_id, confirmed_at IS NULL, requested_at DESC"
        )
    ).fetchall()
    seen: set[str] = set()
    for user_id, hospital_id, confirmed_at in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        conn.execute(
            sa.text(
                "UPDATE patient_profiles "
                "SET preferred_hospital_id = :h, hospital_confirmed_at = :c WHERE user_id = :u"
            ),
            {"h": hospital_id, "c": confirmed_at, "u": user_id},
        )
    op.drop_index("ix_patient_hospitals_hospital_id", table_name="patient_hospitals")
    op.drop_index("ix_patient_hospitals_user_id", table_name="patient_hospitals")
    op.drop_table("patient_hospitals")
