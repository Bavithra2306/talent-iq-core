"""Database access for shortlists: scored results, weights, run events and the activity log."""

import uuid
from datetime import datetime

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import CandidateStatus, PipelineStage
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate, usable_candidate
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.repositories.query_helpers import skill_clause


async def find_scored_rows(
    db: AsyncSession, *, job_id: uuid.UUID | None, include_unranked: bool, status_filter: CandidateStatus | None,
    skill: str | None, skills: list[str], education: str | None, location: str | None, category: str | None,
    min_experience: float | None, max_experience: float | None, search: str | None,
) -> list[tuple[MatchingShortlisting, Candidate, list | None]]:
    """(result row, candidate, skills) of everyone scored for one job (or for every job) that passes the filters."""
    query = (
        select(MatchingShortlisting, Candidate, ResumeExtraction.skills)
        .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
        .outerjoin(ResumeExtraction, ResumeExtraction.resume_id == MatchingShortlisting.resume_id)
        .join(Job, Job.id == MatchingShortlisting.job_id)
        .where(MatchingShortlisting.total_score.is_not(None), Job.deleted_at.is_(None), usable_candidate())
    )
    if job_id:
        query = query.where(MatchingShortlisting.job_id == job_id)
    if not include_unranked:
        # Latest run, plus anyone already shortlisted, shared or reviewed for this job.
        query = query.where(MatchingShortlisting.rank.is_not(None)
                            | (MatchingShortlisting.status != CandidateStatus.SOURCED))
    if status_filter:
        query = query.where(MatchingShortlisting.status == status_filter)
    if skill:
        query = query.where(skill_clause(ResumeExtraction.skills, skill))
    for name in skills:
        if name.strip():
            query = query.where(skill_clause(ResumeExtraction.skills, name.strip()))
    if education:
        query = query.where(cast(ResumeExtraction.education, String).ilike(f"%{education.strip()}%"))
    if location:
        query = query.where(Candidate.location.ilike(f"%{location}%"))
    if category:
        query = query.where(Candidate.category == category)
    if min_experience is not None:
        query = query.where(Candidate.total_years_experience >= min_experience)
    if max_experience is not None:
        query = query.where(Candidate.total_years_experience <= max_experience)
    if search:
        pattern = f"%{search}%"
        query = query.where(Candidate.name.ilike(pattern) | Candidate.email.ilike(pattern)
                            | Candidate.current_title.ilike(pattern))
    return [tuple(r) for r in (await db.execute(query)).all()]


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await db.get(Job, job_id)


async def find_ranked_rows(db: AsyncSession, job_id: uuid.UUID) -> list[tuple[MatchingShortlisting, Candidate]]:
    """(result row, candidate) of the job's current run: the ranked candidates."""
    return [tuple(r) for r in (await db.execute(
        select(MatchingShortlisting, Candidate)
        .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
        .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.rank.is_not(None), usable_candidate())
    )).all()]


async def find_rows_by_job(db: AsyncSession, job_id: uuid.UUID) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting).where(MatchingShortlisting.job_id == job_id)
    )).scalars().all())


async def find_rows_by_ids(db: AsyncSession, row_ids: list[uuid.UUID]) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting).where(MatchingShortlisting.id.in_(row_ids))
    )).scalars())


async def find_run_events(db: AsyncSession, run_id: str) -> list[AuditLog]:
    """Every audit event of one matching run, oldest first."""
    return list((await db.execute(
        select(AuditLog).where(AuditLog.input_data.like(f'%"run_id": "{run_id}"%'))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all())


async def search_logs(
    db: AsyncSession, *, job_id: uuid.UUID | None, stage: PipelineStage | None, category: str | None,
    since: datetime | None, until: datetime | None, only_stages: bool, limit: int, offset: int,
) -> tuple[list[AuditLog], int]:
    """One page of the activity log (newest first) and how many events match in all."""
    query = select(AuditLog)
    if job_id:
        query = query.where(AuditLog.job_id == job_id)
    if stage:
        query = query.where(AuditLog.stage == stage)
    elif only_stages:
        query = query.where(AuditLog.stage.is_not(None))
    if category:
        query = query.where(cast(AuditLog.action_category, String) == category)
    if since:
        query = query.where(AuditLog.created_at >= since)
    if until:
        query = query.where(AuditLog.created_at <= until)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset)
    )).scalars().all()
    return list(rows), total
