"""Database access for candidates: batch events, listing with filters, detail and counts."""

import uuid

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import CandidateStatus
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.repositories.query_helpers import skill_clause


async def find_batch_events(db: AsyncSession, batch_id: str) -> list[AuditLog]:
    """Every audit event of an upload or sourcing batch, oldest first."""
    return list((await db.execute(
        select(AuditLog)
        .where(AuditLog.input_data.like(f'%"batch_id": "{batch_id}"%'))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all())


def _apply_filters(query, *, category, search, location, skill, min_experience,
                   max_experience, status_filter, source_site):
    if category:
        query = query.where(Candidate.category == category)
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Candidate.name.ilike(pattern), Candidate.email.ilike(pattern),
                                Candidate.current_title.ilike(pattern), Candidate.location.ilike(pattern),
                                cast(ResumeExtraction.skills, String).ilike(pattern)))
    if location:
        query = query.where(Candidate.location.ilike(f"%{location}%"))
    if skill:
        query = query.where(skill_clause(ResumeExtraction.skills, skill))
    if min_experience is not None:
        query = query.where(Candidate.total_years_experience >= min_experience)
    if max_experience is not None:
        query = query.where(Candidate.total_years_experience <= max_experience)
    if status_filter == "new":
        # Not matched to any job yet.
        query = query.where(~select(MatchingShortlisting.id)
                            .where(MatchingShortlisting.candidate_id == Candidate.id).exists())
    elif status_filter:
        # Has this status for at least one job.
        query = query.where(select(MatchingShortlisting.id).where(
            MatchingShortlisting.candidate_id == Candidate.id,
            MatchingShortlisting.status == CandidateStatus(status_filter),
        ).exists())
    if source_site:
        query = query.where(Document.source_site == source_site)
    return query


async def search(
    db: AsyncSession, *, category, search, location, skill, min_experience, max_experience,
    status_filter, source_site, sort_column, descending: bool, limit: int, offset: int,
) -> tuple[list[tuple[Candidate, list | None, str | None]], int]:
    """One page of (candidate, skills, source site) rows, and how many candidates match in all."""
    base = (
        select(Candidate, ResumeExtraction.skills, Document.source_site)
        .join(Document, Document.id == Candidate.current_resume_id)
        .outerjoin(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
    )
    base = _apply_filters(
        base, category=category, search=search, location=location, skill=skill,
        min_experience=min_experience, max_experience=max_experience,
        status_filter=status_filter, source_site=source_site,
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    column = {
        "date_added": Candidate.created_at,
        "name": func.lower(Candidate.name),
        "experience": Candidate.total_years_experience,
    }[sort_column]
    ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
    rows = (await db.execute(base.order_by(ordering, Candidate.id).limit(limit).offset(offset))).all()
    return [tuple(r) for r in rows], total


async def find_job_status_rows(db: AsyncSession, candidate_ids: list[uuid.UUID]):
    """(candidate id, job id, job title, status, total score) per candidate and job, best score first."""
    rows = await db.execute(
        select(MatchingShortlisting.candidate_id, MatchingShortlisting.job_id, Job.title,
               MatchingShortlisting.status, MatchingShortlisting.total_score)
        .join(Job, Job.id == MatchingShortlisting.job_id)
        .where(MatchingShortlisting.candidate_id.in_(candidate_ids), Job.deleted_at.is_(None))
        .order_by(MatchingShortlisting.total_score.desc().nulls_last())
    )
    return rows.all()


async def find_detail_row(db: AsyncSession, candidate_id: uuid.UUID):
    """(candidate, extraction, resume document) of one candidate, or None."""
    return (await db.execute(
        select(Candidate, ResumeExtraction, Document)
        .join(Document, Document.id == Candidate.current_resume_id)
        .outerjoin(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .where(Candidate.id == candidate_id)
    )).first()


async def count_by_category(db: AsyncSession) -> list[tuple[str | None, int]]:
    rows = await db.execute(
        select(Candidate.category, func.count()).group_by(Candidate.category).order_by(func.count().desc())
    )
    return [(c, n) for c, n in rows.all()]


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    return await db.get(Candidate, candidate_id)


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)
