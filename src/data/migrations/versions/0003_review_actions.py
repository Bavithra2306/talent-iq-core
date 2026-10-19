"""review actions in the audit-log actions enum

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ACTIONS = (
    'candidate_removed_from_shortlist', 'notes_updated', 'shortlist_shared',
    'candidate_approved', 'candidate_rejected', 'interview_scheduled', 'shortlist_returned',
)


def upgrade() -> None:
    for value in NEW_ACTIONS:
        op.execute(f"ALTER TYPE actions ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres can't drop enum values: rebuild the type without them.
    op.execute(
        "DELETE FROM audit_logs WHERE actions::text IN ("
        + ", ".join(f"'{v}'" for v in NEW_ACTIONS) + ")"
    )
    op.execute("ALTER TYPE actions RENAME TO actions_old")
    op.execute(
        "CREATE TYPE actions AS ENUM ("
        "'sign_in', 'sign_up', 'sign_out', 'password_changed', "
        "'scraping_started', 'scraping_completed', 'scraping_partially_completed', 'scraping_failed', "
        "'parsing_started', 'parsing_completed', 'parsing_failed', "
        "'extraction_started', 'extraction_completed', 'extraction_failed', "
        "'scoring_started', 'scoring_finished', 'scoring_failed', "
        "'shortlisting_created', 'candidate_added_to_shortlist', "
        "'shortlist_export_started', 'shortlist_export_completed', 'shortlist_export_failed')"
    )
    op.execute("ALTER TABLE audit_logs ALTER COLUMN actions TYPE actions USING actions::text::actions")
    op.execute("DROP TYPE actions_old")
