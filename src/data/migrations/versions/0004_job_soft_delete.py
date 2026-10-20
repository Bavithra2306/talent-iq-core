"""soft delete for jobs

A deleted job disappears from the app but stays in the database with its
document, match results and history.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("deleted_by", sa.Uuid(), nullable=True))
    op.create_index("ix_jobs_active", "jobs", ["created_at"],
                    postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_jobs_active", table_name="jobs")
    op.drop_column("jobs", "deleted_by")
    op.drop_column("jobs", "deleted_at")
