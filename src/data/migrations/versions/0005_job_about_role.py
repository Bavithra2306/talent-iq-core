"""about the role: a short description of the job

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("about_role", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "about_role")
