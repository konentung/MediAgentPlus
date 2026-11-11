"""MediAgent+ 完整資料庫結構（2026-09-28 由歷次 migration 統整為單一版本）。

所有資料表以 `app/models/` 為準（欄位說明見 docs/database-models.md）。
PostgreSQL 需要 pgvector 擴充（知識庫向量檢索）；SQLite 只用於測試（`db.create_all()`），
JSONB / TSVECTOR / VECTOR 皆以 `with_variant` 退回 JSON / Text。

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')  # knowledge_chunks.embedding
    op.create_table('drugs',
    sa.Column('nhi_code', sa.String(length=12), nullable=False),
    sa.Column('name_en', sa.String(length=150), nullable=False),
    sa.Column('name_zh', sa.String(length=150), nullable=True),
    sa.Column('ingredient', sa.String(length=300), nullable=True),
    sa.Column('dosage_form', sa.String(length=50), nullable=True),
    sa.Column('atc_code', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('nhi_code')
    )
    with op.batch_alter_table('drugs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_drugs_atc_code'), ['atc_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_drugs_name_en'), ['name_en'], unique=False)
        batch_op.create_index(batch_op.f('ix_drugs_name_zh'), ['name_zh'], unique=False)

    op.create_table('er_snapshots',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('nhi_hospital_id', sa.String(length=16), nullable=False),
    sa.Column('hospital_name', sa.String(length=100), nullable=False),
    sa.Column('area_code', sa.String(length=4), nullable=False),
    sa.Column('hosp_type', sa.String(length=2), nullable=False),
    sa.Column('full_119', sa.Boolean(), nullable=False),
    sa.Column('wait_see', sa.Integer(), nullable=False),
    sa.Column('wait_bed', sa.Integer(), nullable=False),
    sa.Column('wait_admit', sa.Integer(), nullable=False),
    sa.Column('wait_icu', sa.Integer(), nullable=False),
    sa.Column('reported_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nhi_hospital_id', 'reported_at', name='uq_er_snapshot_slot')
    )
    with op.batch_alter_table('er_snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_er_snapshots_area_reported', ['area_code', 'reported_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_er_snapshots_nhi_hospital_id'), ['nhi_hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_er_snapshots_reported_at'), ['reported_at'], unique=False)

    op.create_table('hospitals',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('region', sa.String(length=50), nullable=True),
    sa.Column('address', sa.String(length=200), nullable=True),
    sa.Column('contact_phone', sa.String(length=30), nullable=True),
    sa.Column('capacity_tier', sa.String(length=16), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lng', sa.Float(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('knowledge_jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('pharmacies',
    sa.Column('id', sa.String(length=24), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('address', sa.String(length=200), nullable=True),
    sa.Column('city', sa.String(length=10), nullable=True),
    sa.Column('district', sa.String(length=10), nullable=True),
    sa.Column('nhi_contracted', sa.Boolean(), nullable=False),
    sa.Column('opening_hours', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pharmacies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pharmacies_city'), ['city'], unique=False)
        batch_op.create_index(batch_op.f('ix_pharmacies_district'), ['district'], unique=False)

    op.create_table('audit_logs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('action', sa.String(length=48), nullable=False),
    sa.Column('target_user_id', sa.String(length=32), nullable=True),
    sa.Column('hospital_id', sa.String(length=32), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_hospital_id'), ['hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_target_user_id'), ['target_user_id'], unique=False)

    op.create_table('knowledge_documents',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('hospital_id', sa.String(length=32), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('source', sa.String(length=500), nullable=True),
    sa.Column('content_hash', sa.String(length=40), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_documents_content_hash'), ['content_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_documents_hospital_id'), ['hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_documents_source'), ['source'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('line_user_id', sa.String(length=64), nullable=True),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('picture_url', sa.String(length=512), nullable=True),
    sa.Column('role', sa.Enum('GUEST', 'PATIENT', 'FAMILY', 'HOSPITAL_STAFF', 'MEDICAL_STAFF', 'HOSPITAL_ADMIN', 'SUPER_ADMIN', name='role', native_enum=False, length=32), nullable=False),
    sa.Column('binding_status', sa.Enum('UNBOUND', 'PENDING', 'VERIFIED', 'LOCKED', name='binding_status', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('hospital_id', sa.String(length=32), nullable=True),
    sa.Column('push_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_hospital_id'), ['hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_line_user_id'), ['line_user_id'], unique=True)

    op.create_table('feedback',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=True),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('hospital_id', sa.String(length=32), nullable=True),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('contact', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('resolved_by', sa.String(length=32), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolver_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feedback_hospital_id'), ['hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_feedback_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_feedback_user_id'), ['user_id'], unique=False)

    op.create_table('knowledge_chunks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('document_id', sa.String(length=32), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tokens', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR().with_variant(sa.Text(), 'sqlite'), nullable=True),
    sa.Column('embedding', Vector(1536).with_variant(sa.JSON(none_as_null=True), 'sqlite'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_chunks_document_id'), ['document_id'], unique=False)
        batch_op.create_index('ix_knowledge_chunks_embedding', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
        batch_op.create_index('ix_knowledge_chunks_search_vector', ['search_vector'], unique=False, postgresql_using='gin')

    op.create_table('patient_allergies',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('substance', sa.String(length=100), nullable=False),
    sa.Column('reaction', sa.String(length=16), nullable=True),
    sa.Column('severity', sa.String(length=8), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_allergies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_allergies_substance'), ['substance'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_allergies_user_id'), ['user_id'], unique=False)

    op.create_table('patient_conditions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=True),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('hospital_name', sa.String(length=100), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_conditions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_conditions_code'), ['code'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_conditions_user_id'), ['user_id'], unique=False)

    op.create_table('patient_consents',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('consent_type', sa.String(length=32), nullable=False),
    sa.Column('granted', sa.Boolean(), nullable=False),
    sa.Column('consent_version', sa.String(length=16), nullable=False),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('line_user_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_consents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_consents_user_id'), ['user_id'], unique=False)

    op.create_table('patient_emergency_contacts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('relationship', sa.String(length=16), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_emergency_contacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_emergency_contacts_user_id'), ['user_id'], unique=False)

    op.create_table('patient_form_submissions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('form_type', sa.String(length=16), nullable=False),
    sa.Column('answers', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reviewed_by', sa.String(length=32), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_form_submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_form_submissions_user_id'), ['user_id'], unique=False)

    op.create_table('patient_medications',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('drug_name', sa.String(length=100), nullable=False),
    sa.Column('dose', sa.String(length=50), nullable=True),
    sa.Column('frequency', sa.String(length=8), nullable=True),
    sa.Column('purpose', sa.String(length=100), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_medications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_medications_drug_name'), ['drug_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_medications_user_id'), ['user_id'], unique=False)

    op.create_table('patient_profiles',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('filler_relation', sa.String(length=16), nullable=True),
    sa.Column('filler_name', sa.String(length=50), nullable=True),
    sa.Column('filler_relationship', sa.String(length=16), nullable=True),
    sa.Column('filler_phone', sa.String(length=20), nullable=True),
    sa.Column('full_name', sa.String(length=50), nullable=True),
    sa.Column('national_id_last4', sa.String(length=4), nullable=True),
    sa.Column('national_id_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('sex', sa.String(length=8), nullable=True),
    sa.Column('preferred_language', sa.String(length=16), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('address_city', sa.String(length=20), nullable=True),
    sa.Column('address_district', sa.String(length=20), nullable=True),
    sa.Column('address_street', sa.String(length=200), nullable=True),
    sa.Column('height_cm', sa.Numeric(precision=5, scale=1), nullable=True),
    sa.Column('weight_kg', sa.Numeric(precision=5, scale=1), nullable=True),
    sa.Column('blood_type', sa.String(length=8), nullable=True),
    sa.Column('rh', sa.String(length=8), nullable=True),
    sa.Column('pregnancy_status', sa.String(length=16), nullable=True),
    sa.Column('smoking', sa.String(length=8), nullable=True),
    sa.Column('smoking_per_day', sa.Integer(), nullable=True),
    sa.Column('drinking', sa.String(length=16), nullable=True),
    sa.Column('betel_nut', sa.String(length=8), nullable=True),
    sa.Column('drug_allergies_none', sa.Boolean(), nullable=True),
    sa.Column('food_allergies_none', sa.Boolean(), nullable=True),
    sa.Column('chronic_conditions_none', sa.Boolean(), nullable=True),
    sa.Column('other_allergies', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('condition_notes', sa.String(length=500), nullable=True),
    sa.Column('has_current_medications', sa.String(length=8), nullable=True),
    sa.Column('supplements', sa.String(length=300), nullable=True),
    sa.Column('preferred_hospital_id', sa.String(length=32), nullable=True),
    sa.Column('hospital_confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('nhi_status', sa.String(length=16), nullable=True),
    sa.Column('catastrophic_illness_card', sa.Boolean(), nullable=True),
    sa.Column('disability_card', sa.Boolean(), nullable=True),
    sa.Column('accessibility_needs', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('completed_steps', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['preferred_hospital_id'], ['hospitals.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    with op.batch_alter_table('patient_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_profiles_national_id_fingerprint'), ['national_id_fingerprint'], unique=True)
        batch_op.create_index(batch_op.f('ix_patient_profiles_phone'), ['phone'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_profiles_preferred_hospital_id'), ['preferred_hospital_id'], unique=False)

    op.create_table('patient_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('hospital_id', sa.String(length=32), nullable=False),
    sa.Column('medical_record_no', sa.String(length=32), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('identity_method', sa.String(length=16), nullable=True),
    sa.Column('identity_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('identity_verified_by', sa.String(length=32), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hospital_id'], ['hospitals.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'hospital_id', name='uq_patient_record_scope')
    )
    with op.batch_alter_table('patient_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_records_hospital_id'), ['hospital_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_records_medical_record_no'), ['medical_record_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_records_user_id'), ['user_id'], unique=False)

    op.create_table('prescriptions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('hospital_name', sa.String(length=100), nullable=True),
    sa.Column('issued_on', sa.Date(), nullable=True),
    sa.Column('days_supply', sa.Integer(), nullable=True),
    sa.Column('is_chronic_refill', sa.Boolean(), nullable=False),
    sa.Column('refill_seq', sa.Integer(), nullable=True),
    sa.Column('refill_total', sa.Integer(), nullable=True),
    sa.Column('refill_reminder_on', sa.Date(), nullable=True),
    sa.Column('refill_reminded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('items', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('image_path', sa.String(length=255), nullable=True),
    sa.Column('image_content_type', sa.String(length=64), nullable=True),
    sa.Column('line_message_id', sa.String(length=64), nullable=True),
    sa.Column('ocr_model', sa.String(length=64), nullable=True),
    sa.Column('ocr_raw', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('prescriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_prescriptions_user_id'), ['user_id'], unique=False)

    op.create_table('medication_schedules',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('prescription_id', sa.String(length=32), nullable=True),
    sa.Column('drug_name', sa.String(length=150), nullable=False),
    sa.Column('dose_text', sa.String(length=50), nullable=True),
    sa.Column('frequency_code', sa.String(length=8), nullable=False),
    sa.Column('timing', sa.String(length=8), nullable=True),
    sa.Column('times', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('paused_until', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['prescription_id'], ['prescriptions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('medication_schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_medication_schedules_user_id'), ['user_id'], unique=False)

    op.create_table('patient_encounters',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('encounter_type', sa.String(length=16), nullable=False),
    sa.Column('visit_date', sa.Date(), nullable=False),
    sa.Column('department', sa.String(length=50), nullable=True),
    sa.Column('attending_physician', sa.String(length=50), nullable=True),
    sa.Column('diagnoses', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('admitted_at', sa.Date(), nullable=True),
    sa.Column('discharged_at', sa.Date(), nullable=True),
    sa.Column('discharge_code', sa.String(length=32), nullable=True),
    sa.Column('patient_visible_summary', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['patient_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_encounters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_encounters_discharge_code'), ['discharge_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_encounters_record_id'), ['record_id'], unique=False)

    op.create_table('patient_follow_ups',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('assignee_id', sa.String(length=32), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['patient_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_follow_ups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_follow_ups_due_date'), ['due_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_follow_ups_record_id'), ['record_id'], unique=False)

    op.create_table('patient_record_notes',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('visibility', sa.String(length=16), nullable=False),
    sa.Column('author_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['patient_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_record_notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_record_notes_record_id'), ['record_id'], unique=False)

    op.create_table('patient_record_reviews',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('reviewed_by', sa.String(length=32), nullable=False),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('corrections', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['patient_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_record_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_record_reviews_record_id'), ['record_id'], unique=False)

    op.create_table('patient_risk_flags',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('flag_type', sa.String(length=32), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['patient_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_risk_flags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_patient_risk_flags_record_id'), ['record_id'], unique=False)

    op.create_table('reminder_deliveries',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('schedule_id', sa.String(length=32), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('snooze_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['schedule_id'], ['medication_schedules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('schedule_id', 'scheduled_for', name='uq_delivery_slot')
    )
    with op.batch_alter_table('reminder_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reminder_deliveries_schedule_id'), ['schedule_id'], unique=False)



def downgrade():
    with op.batch_alter_table('reminder_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reminder_deliveries_schedule_id'))

    op.drop_table('reminder_deliveries')
    with op.batch_alter_table('patient_risk_flags', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_risk_flags_record_id'))

    op.drop_table('patient_risk_flags')
    with op.batch_alter_table('patient_record_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_record_reviews_record_id'))

    op.drop_table('patient_record_reviews')
    with op.batch_alter_table('patient_record_notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_record_notes_record_id'))

    op.drop_table('patient_record_notes')
    with op.batch_alter_table('patient_follow_ups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_follow_ups_record_id'))
        batch_op.drop_index(batch_op.f('ix_patient_follow_ups_due_date'))

    op.drop_table('patient_follow_ups')
    with op.batch_alter_table('patient_encounters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_encounters_record_id'))
        batch_op.drop_index(batch_op.f('ix_patient_encounters_discharge_code'))

    op.drop_table('patient_encounters')
    with op.batch_alter_table('medication_schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_medication_schedules_user_id'))

    op.drop_table('medication_schedules')
    with op.batch_alter_table('prescriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_prescriptions_user_id'))

    op.drop_table('prescriptions')
    with op.batch_alter_table('patient_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_records_user_id'))
        batch_op.drop_index(batch_op.f('ix_patient_records_medical_record_no'))
        batch_op.drop_index(batch_op.f('ix_patient_records_hospital_id'))

    op.drop_table('patient_records')
    with op.batch_alter_table('patient_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_profiles_preferred_hospital_id'))
        batch_op.drop_index(batch_op.f('ix_patient_profiles_phone'))
        batch_op.drop_index(batch_op.f('ix_patient_profiles_national_id_fingerprint'))

    op.drop_table('patient_profiles')
    with op.batch_alter_table('patient_medications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_medications_user_id'))
        batch_op.drop_index(batch_op.f('ix_patient_medications_drug_name'))

    op.drop_table('patient_medications')
    with op.batch_alter_table('patient_form_submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_form_submissions_user_id'))

    op.drop_table('patient_form_submissions')
    with op.batch_alter_table('patient_emergency_contacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_emergency_contacts_user_id'))

    op.drop_table('patient_emergency_contacts')
    with op.batch_alter_table('patient_consents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_consents_user_id'))

    op.drop_table('patient_consents')
    with op.batch_alter_table('patient_conditions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_conditions_user_id'))
        batch_op.drop_index(batch_op.f('ix_patient_conditions_code'))

    op.drop_table('patient_conditions')
    with op.batch_alter_table('patient_allergies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_allergies_user_id'))
        batch_op.drop_index(batch_op.f('ix_patient_allergies_substance'))

    op.drop_table('patient_allergies')
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.drop_index('ix_knowledge_chunks_search_vector', postgresql_using='gin')
        batch_op.drop_index('ix_knowledge_chunks_embedding', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
        batch_op.drop_index(batch_op.f('ix_knowledge_chunks_document_id'))

    op.drop_table('knowledge_chunks')
    with op.batch_alter_table('feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feedback_user_id'))
        batch_op.drop_index(batch_op.f('ix_feedback_status'))
        batch_op.drop_index(batch_op.f('ix_feedback_hospital_id'))

    op.drop_table('feedback')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_line_user_id'))
        batch_op.drop_index(batch_op.f('ix_users_hospital_id'))

    op.drop_table('users')
    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_documents_source'))
        batch_op.drop_index(batch_op.f('ix_knowledge_documents_hospital_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_documents_content_hash'))

    op.drop_table('knowledge_documents')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_target_user_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_hospital_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_actor_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('pharmacies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pharmacies_district'))
        batch_op.drop_index(batch_op.f('ix_pharmacies_city'))

    op.drop_table('pharmacies')
    op.drop_table('knowledge_jobs')
    op.drop_table('hospitals')
    with op.batch_alter_table('er_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_er_snapshots_reported_at'))
        batch_op.drop_index(batch_op.f('ix_er_snapshots_nhi_hospital_id'))
        batch_op.drop_index('ix_er_snapshots_area_reported')

    op.drop_table('er_snapshots')
    with op.batch_alter_table('drugs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_drugs_name_zh'))
        batch_op.drop_index(batch_op.f('ix_drugs_name_en'))
        batch_op.drop_index(batch_op.f('ix_drugs_atc_code'))

    op.drop_table('drugs')
