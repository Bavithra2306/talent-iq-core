"""role name instead of a role description (e.g. "SDE II")

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("jobs", "about_role")
    op.add_column("jobs", sa.Column("role_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "role_name")
    op.add_column("jobs", sa.Column("about_role", sa.Text(), nullable=True))
