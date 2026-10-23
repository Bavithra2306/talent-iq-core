"""Database access for the audit log."""

import uuid
from datetime import datetime

from src.constants.enums import Action, ActionCategory, PipelineStage
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.audit_log import AuditLog


async def add_event(
    *,
    user_id: uuid.UUID | None,
    category: ActionCategory | None,
    action: Action | None,
    stage: PipelineStage | None,
    job_id: uuid.UUID | None,
    input_data: str | None,
    output_data: str | None,
    start_time: datetime | None,
    end_time: datetime,
    duration: int | None,
) -> None:
    """Write one event in its own session, so it is kept even when the caller's transaction is rolled back."""
    async with AsyncSessionLocal() as db:
        db.add(
            AuditLog(
                user_id=user_id,
                action_category=category,
                actions=action,
                stage=stage,
                job_id=job_id,
                input_data=input_data,
                output_data=output_data,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                created_by=user_id,
            )
        )
        await db.commit()
