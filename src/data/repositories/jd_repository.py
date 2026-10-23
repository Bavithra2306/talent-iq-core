"""Database access for jobs, their job description documents and their scoring settings."""

import uuid

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import JobStatus
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.scoring_config import ScoringConfig


def _active():
    return Job.deleted_at.is_(None)


# ---------- documents ----------

async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)


async def find_document_by_hash(db: AsyncSession, file_hash: str) -> Document | None:
    return (await db.execute(select(Document).where(Document.file_hash == file_hash))).scalar_one_or_none()


async def add_document(db: AsyncSession, document: Document) -> None:
    db.add(document)
    await db.flush()


async def delete_document(db: AsyncSession, document: Document) -> None:
    await db.delete(document)


# ---------- jobs ----------

async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """The job by id, deleted or not."""
    return await db.get(Job, job_id)


async def find_active_job_id_by_document(db: AsyncSession, document_id: uuid.UUID) -> uuid.UUID | None:
    return (await db.execute(
        select(Job.id).where(Job.jd_id == document_id, _active()).limit(1)
    )).scalar_one_or_none()


async def find_active_job_by_document(db: AsyncSession, document_id: uuid.UUID) -> Job | None:
    return (await db.execute(
        select(Job).where(Job.jd_id == document_id, _active()).limit(1)
    )).scalar_one_or_none()


async def find_active_job_by_title(db: AsyncSession, title: str, category: str | None) -> Job | None:
    """An active job with this title (any letter case) in this category (or both without one)."""
    return (await db.execute(
        select(Job).where(_active(), func.lower(Job.title) == title.strip().lower(),
                          Job.category.is_not_distinct_from(category)).limit(1)
    )).scalar_one_or_none()


async def search_jobs(
    db: AsyncSession, *, status_filter: JobStatus | None, category: str | None, search: str | None,
    location: str | None, min_experience: int | None, max_experience: int | None, limit: int, offset: int,
) -> tuple[list[Job], int]:
    query = select(Job).where(_active())
    if status_filter:
        query = query.where(Job.status == status_filter)
    if category:
        query = query.where(Job.category.ilike(category))
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Job.title.ilike(pattern), Job.role_name.ilike(pattern), Job.location.ilike(pattern),
                                cast(Job.skills, String).ilike(pattern)))
    if location:
        query = query.where(Job.location.ilike(f"{location.strip()}%"))  # "Chennai" also finds "Chennai, India"
    if min_experience is not None:
        query = query.where(Job.experience_years >= min_experience)
    if max_experience is not None:
        query = query.where(Job.experience_years <= max_experience)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await db.execute(query.order_by(Job.created_at.desc()).limit(limit).offset(offset))
    return list(rows.scalars()), total


async def add_job(db: AsyncSession, job: Job) -> None:
    db.add(job)
    await db.flush()


def add_scoring_config(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """The job's default weights (skill 40, role 20, experience 25, education 15)."""
    db.add(ScoringConfig(job_id=job_id, created_by=user_id))


async def find_scoring_config(db: AsyncSession, job_id: uuid.UUID) -> ScoringConfig | None:
    return (await db.execute(select(ScoringConfig).where(ScoringConfig.job_id == job_id))).scalar_one_or_none()


def save_scoring_config(db: AsyncSession, config: ScoringConfig) -> None:
    db.add(config)


async def refresh(db: AsyncSession, job: Job) -> None:
    await db.refresh(job)
