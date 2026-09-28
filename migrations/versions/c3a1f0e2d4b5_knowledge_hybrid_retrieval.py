"""knowledge: hybrid retrieval (tsvector + pgvector), document meta / hash

Revision ID: c3a1f0e2d4b5
Revises: 11b935652f8c
Create Date: 2026-09-28 10:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c3a1f0e2d4b5'
down_revision = '11b935652f8c'
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 1536


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade():
    postgres = _is_postgres()
    if postgres:
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.alter_column('source', type_=sa.String(length=500), existing_type=sa.String(length=200))
        batch_op.add_column(sa.Column('content_hash', sa.String(length=40), nullable=True))
        batch_op.add_column(
            sa.Column(
                'meta',
                postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
                nullable=True,
            )
        )
        batch_op.create_index(batch_op.f('ix_knowledge_documents_content_hash'), ['content_hash'], unique=False)

    if postgres:
        op.add_column('knowledge_chunks', sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True))
        op.execute(f'ALTER TABLE knowledge_chunks ADD COLUMN embedding vector({EMBEDDING_DIMENSIONS})')
        # 既有片段：用已存的 tokens 補 tsvector
        op.execute(
            "UPDATE knowledge_chunks c SET search_vector = to_tsvector('simple', "
            "COALESCE((SELECT string_agg(t, ' ') FROM jsonb_array_elements_text(c.tokens) t), ''))"
        )
        op.execute(
            'CREATE INDEX ix_knowledge_chunks_search_vector ON knowledge_chunks USING gin (search_vector)'
        )
        op.execute(
            'CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks '
            'USING hnsw (embedding vector_cosine_ops)'
        )
    else:
        with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
            batch_op.add_column(sa.Column('search_vector', sa.Text(), nullable=True))
            batch_op.add_column(sa.Column('embedding', sa.JSON(), nullable=True))


def downgrade():
    postgres = _is_postgres()
    if postgres:
        op.execute('DROP INDEX IF EXISTS ix_knowledge_chunks_embedding')
        op.execute('DROP INDEX IF EXISTS ix_knowledge_chunks_search_vector')
    with op.batch_alter_table('knowledge_chunks', schema=None) as batch_op:
        batch_op.drop_column('embedding')
        batch_op.drop_column('search_vector')
    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_documents_content_hash'))
        batch_op.drop_column('meta')
        batch_op.drop_column('content_hash')
        batch_op.alter_column('source', type_=sa.String(length=200), existing_type=sa.String(length=500))
