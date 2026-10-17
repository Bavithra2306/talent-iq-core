"""candidate category, document source_site, audit stage/job_id, 768-dim vectors, interview status

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PIPELINE_STAGES = (
    'created', 'collecting', 'reading', 'scoring',
    'shortlisted', 'exported', 'partly_completed', 'failed',
)

# (table, column) pairs holding embeddings
VECTOR_COLUMNS = (
    ('job_embeddings', 'skill_vector'),
    ('job_embeddings', 'role_vector'),
    ('job_embeddings', 'nice_to_have_skill_vector'),
    ('resume_embeddings', 'vector'),
)


def upgrade() -> None:
    op.add_column('candidates', sa.Column('category', sa.String(), nullable=True))
    op.create_index('ix_candidates_category', 'candidates', ['category'], unique=False)

    op.add_column('documents', sa.Column('source_site', sa.String(), nullable=True))

    pipeline_stage = postgresql.ENUM(*PIPELINE_STAGES, name='pipeline_stage')
    pipeline_stage.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'audit_logs',
        sa.Column(
            'stage',
            postgresql.ENUM(*PIPELINE_STAGES, name='pipeline_stage', create_type=False),
            nullable=True,
        ),
    )
    op.add_column('audit_logs', sa.Column('job_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_audit_logs_job_id'), 'audit_logs', ['job_id'], unique=False)

    op.execute("ALTER TYPE candidate_status ADD VALUE IF NOT EXISTS 'interview_scheduled'")

    # Existing vectors (if any) can't be converted between dimensions; they are cleared
    # and must be re-embedded with the 768-dim model.
    for table, column in VECTOR_COLUMNS:
        op.alter_column(
            table, column,
            type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
            existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=1536),
            postgresql_using='NULL',
        )


def downgrade() -> None:
    for table, column in VECTOR_COLUMNS:
        op.alter_column(
            table, column,
            type_=pgvector.sqlalchemy.vector.VECTOR(dim=1536),
            existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
            postgresql_using='NULL',
        )

    # Postgres can't drop a single enum value: move rows off it, then rebuild the type.
    op.execute(
        "UPDATE candidates SET status = 'approved' WHERE status = 'interview_scheduled'"
    )
    op.execute(
        "UPDATE matching_shortlisting SET status = 'approved' WHERE status = 'interview_scheduled'"
    )
    op.execute("ALTER TYPE candidate_status RENAME TO candidate_status_old")
    op.execute(
        "CREATE TYPE candidate_status AS ENUM ('sourced', 'shortlisted', 'approved', 'rejected')"
    )
    op.execute("ALTER TABLE matching_shortlisting ALTER COLUMN status DROP DEFAULT")
    for table in ('candidates', 'matching_shortlisting'):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN status TYPE candidate_status "
            "USING status::text::candidate_status"
        )
    op.execute("ALTER TABLE matching_shortlisting ALTER COLUMN status SET DEFAULT 'sourced'")
    op.execute("DROP TYPE candidate_status_old")

    op.drop_index(op.f('ix_audit_logs_job_id'), table_name='audit_logs')
    op.drop_column('audit_logs', 'job_id')
    op.drop_column('audit_logs', 'stage')
    op.execute("DROP TYPE IF EXISTS pipeline_stage")

    op.drop_column('documents', 'source_site')

    op.drop_index('ix_candidates_category', table_name='candidates')
    op.drop_column('candidates', 'category')
