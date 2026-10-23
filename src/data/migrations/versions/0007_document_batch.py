"""documents remember the batch that added them (to list what a sourcing run found)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("batch_id", sa.String(), nullable=True))
    op.create_index("ix_documents_batch_id", "documents", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_batch_id", table_name="documents")
    op.drop_column("documents", "batch_id")
