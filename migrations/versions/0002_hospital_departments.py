"""hospitals.departments：診療科別（找附近診所依症狀科別篩選）。

Revision ID: 0002_hospital_departments
Revises: 0001_initial
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_hospital_departments'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('hospitals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('departments', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('hospitals', schema=None) as batch_op:
        batch_op.drop_column('departments')
