"""soft delete for resumes (candidates)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidates", sa.Column("deleted_by", sa.Uuid(), nullable=True))
    op.execute("ALTER TYPE actions ADD VALUE IF NOT EXISTS 'resume_deleted'")


def downgrade() -> None:
    # (the 'resume_deleted' value stays in the enum: Postgres cannot drop one cheaply)
    op.drop_column("candidates", "deleted_by")
    op.drop_column("candidates", "deleted_at")
