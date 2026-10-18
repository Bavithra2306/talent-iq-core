"""Audit / stage logging. Every event is written to audit_logs and to the server log.

Uses its own session so failure events are kept even when the caller's
transaction is rolled back.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from src.constants.enums import Action, ActionCategory, PipelineStage
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.audit_log import AuditLog

logger = logging.getLogger("talentiq.audit")


def utcnow() -> datetime:
    """Naive UTC timestamp (audit_logs uses timestamp without time zone)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_text(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, default=str)[:4000]


async def log_event(
    *,
    user_id: uuid.UUID | None,
    category: ActionCategory | None = None,
    action: Action | None = None,
    stage: PipelineStage | None = None,
    job_id: uuid.UUID | None = None,
    input_data=None,
    output_data=None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> None:
    end_time = end_time or utcnow()
    duration = (
        int((end_time - start_time).total_seconds() * 1000) if start_time else None
    )

    logger.info(
        "audit category=%s action=%s stage=%s job_id=%s user=%s duration_ms=%s output=%s",
        category and category.value,
        action and action.value,
        stage and stage.value,
        job_id,
        user_id,
        duration,
        _to_text(output_data),
    )

    try:
        async with AsyncSessionLocal() as db:
            db.add(
                AuditLog(
                    user_id=user_id,
                    action_category=category,
                    actions=action,
                    stage=stage,
                    job_id=job_id,
                    input_data=_to_text(input_data),
                    output_data=_to_text(output_data),
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    created_by=user_id,
                )
            )
            await db.commit()
    except Exception:
        # Logging must never break the request.
        logger.exception("Failed to write audit log")
