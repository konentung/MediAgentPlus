"""藥局人員與領藥需求：users.pharmacy_id、pharmacies.self_managed_at、prescription_pickups。

Revision ID: 0004_pharmacy_staff_pickups
Revises: 0003_knowledge_department
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_pharmacy_staff_pickups'
down_revision = '0003_knowledge_department'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pharmacy_id', sa.String(length=24), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_pharmacy_id'), ['pharmacy_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_users_pharmacy_id_pharmacies', 'pharmacies', ['pharmacy_id'], ['id'], ondelete='SET NULL'
        )
    with op.batch_alter_table('pharmacies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('self_managed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'prescription_pickups',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('prescription_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('pharmacy_id', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('handled_by', sa.String(length=32), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('picked_up_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['prescription_id'], ['prescriptions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['handled_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('prescription_id', name='uq_pickup_prescription'),
    )
    with op.batch_alter_table('prescription_pickups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_prescription_pickups_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_prescription_pickups_pharmacy_id'), ['pharmacy_id'], unique=False)


def downgrade():
    with op.batch_alter_table('prescription_pickups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_prescription_pickups_pharmacy_id'))
        batch_op.drop_index(batch_op.f('ix_prescription_pickups_user_id'))
    op.drop_table('prescription_pickups')
    with op.batch_alter_table('pharmacies', schema=None) as batch_op:
        batch_op.drop_column('self_managed_at')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_pharmacy_id_pharmacies', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_pharmacy_id'))
        batch_op.drop_column('pharmacy_id')
