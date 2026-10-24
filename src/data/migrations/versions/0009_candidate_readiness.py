"""resume readiness: ready / needs_review / unprocessable, with the reason

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("readiness", sa.String(), nullable=False, server_default="ready"))
    op.add_column("candidates", sa.Column("readiness_reason", sa.String(), nullable=True))
    op.create_index("ix_candidates_readiness", "candidates", ["readiness"])
    op.execute("ALTER TYPE actions ADD VALUE IF NOT EXISTS 'resume_reviewed'")


def downgrade() -> None:
    # (the 'resume_reviewed' value stays in the enum: Postgres cannot drop one cheaply)
    op.drop_index("ix_candidates_readiness", table_name="candidates")
    op.drop_column("candidates", "readiness_reason")
    op.drop_column("candidates", "readiness")
