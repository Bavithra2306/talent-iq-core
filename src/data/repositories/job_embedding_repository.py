"""Database access for the job embeddings."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.job_embedding import JobEmbedding


async def find_by_job(db: AsyncSession, job_id: uuid.UUID) -> JobEmbedding | None:
    return (await db.execute(
        select(JobEmbedding).where(JobEmbedding.job_id == job_id).limit(1)
    )).scalar_one_or_none()


def add(db: AsyncSession, row: JobEmbedding) -> None:
    db.add(row)


async def count_since(db: AsyncSession, start: datetime) -> int:
    """Job embeddings created or updated since a moment (used for the daily quota)."""
    return (await db.execute(
        select(func.count()).where(func.coalesce(JobEmbedding.updated_at, JobEmbedding.created_at) >= start)
    )).scalar_one()
