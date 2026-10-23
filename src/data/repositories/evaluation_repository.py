"""Database access for the evaluation script (answer key, top-10 overlap, field accuracy, timing)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import ActionCategory, PipelineStage
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_extraction import ResumeExtraction


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await db.get(Job, job_id)


async def find_category_resumes(db: AsyncSession, category: str | None) -> list[tuple[Candidate, ResumeExtraction]]:
    return [tuple(r) for r in (await db.execute(
        select(Candidate, ResumeExtraction)
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .where(Candidate.category == category)
    )).all()]


async def find_top_ranked(db: AsyncSession, job_id: uuid.UUID, limit: int) -> list[tuple[MatchingShortlisting, Candidate]]:
    return [tuple(r) for r in (await db.execute(
        select(MatchingShortlisting, Candidate)
        .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
        .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.rank.is_not(None))
        .order_by(MatchingShortlisting.rank).limit(limit)
    )).all()]


async def find_latest_resumes(db: AsyncSession, limit: int) -> list[tuple[Candidate, ResumeExtraction, Document]]:
    return [tuple(r) for r in (await db.execute(
        select(Candidate, ResumeExtraction, Document)
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .join(Document, Document.id == Candidate.current_resume_id)
        .order_by(Document.created_at.desc()).limit(limit)
    )).all()]


async def find_resumes_by_candidates(db: AsyncSession,
                                     candidate_ids: list[uuid.UUID]) -> list[tuple[Candidate, ResumeExtraction]]:
    return [tuple(r) for r in (await db.execute(
        select(Candidate, ResumeExtraction)
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .where(Candidate.id.in_(candidate_ids))
    )).all()]


async def find_latest_run_logs(db: AsyncSession, limit: int) -> list[tuple[AuditLog, str]]:
    """The latest finished matching runs with their job titles."""
    return [tuple(r) for r in (await db.execute(
        select(AuditLog, Job.title).join(Job, Job.id == AuditLog.job_id)
        .where(AuditLog.action_category == ActionCategory.SHORTLISTING,
               AuditLog.stage.in_([PipelineStage.SHORTLISTED, PipelineStage.PARTLY_COMPLETED]))
        .order_by(AuditLog.created_at.desc()).limit(limit)
    )).all()]
