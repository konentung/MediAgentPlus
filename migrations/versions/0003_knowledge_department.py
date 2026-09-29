"""knowledge_documents.department（衛教科別，檢索加權）、knowledge_chunks.heading / kind（標題路徑、片段型態）。

Revision ID: 0003_knowledge_department
Revises: 0002_hospital_departments
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003_knowledge_department'
down_revision = '0002_hospital_departments'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('department', sa.String(length=50), nullable=True))
        batch_op.create_index(batch_op.f('ix_knowledge_documents_department'), ['department'], unique=False)
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('heading', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('kind', sa.String(length=8), nullable=False, server_default='text'))


def downgrade():
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.drop_column('kind')
        batch_op.drop_column('heading')
    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_documents_department'))
        batch_op.drop_column('department')
