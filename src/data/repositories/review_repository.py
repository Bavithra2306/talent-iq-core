"""Database access for the review between recruiter and hiring manager."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import Action, CandidateStatus
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_extraction import ResumeExtraction


async def find_interview_events(db: AsyncSession) -> list[tuple[str | None, datetime]]:
    """(input, time) of every "interview scheduled" event, oldest first."""
    rows = (await db.execute(
        select(AuditLog.input_data, AuditLog.created_at)
        .where(AuditLog.actions == Action.INTERVIEW_SCHEDULED)
        .order_by(AuditLog.created_at)
    )).all()
    return [(data, at) for data, at in rows]


async def get_entry(db: AsyncSession, entry_id: uuid.UUID) -> MatchingShortlisting | None:
    return await db.get(MatchingShortlisting, entry_id)


async def refresh(db: AsyncSession, row: MatchingShortlisting) -> None:
    await db.refresh(row)


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    return await db.get(Candidate, candidate_id)


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await db.get(Job, job_id)


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)


async def get_resume_skills(db: AsyncSession, resume_id: uuid.UUID) -> list | None:
    return (await db.execute(
        select(ResumeExtraction.skills).where(ResumeExtraction.resume_id == resume_id).limit(1)
    )).scalar_one_or_none()


async def find_entries_to_share(db: AsyncSession, job_id: uuid.UUID,
                                entry_ids: list[uuid.UUID] | None) -> list[MatchingShortlisting]:
    """The chosen entries of a job, or all its shortlisted entries when none were chosen."""
    query = (select(MatchingShortlisting).join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
             .where(MatchingShortlisting.job_id == job_id, Candidate.deleted_at.is_(None)))
    if entry_ids:
        query = query.where(MatchingShortlisting.id.in_(entry_ids))
    else:
        query = query.where(MatchingShortlisting.status == CandidateStatus.SHORTLISTED)
    return list((await db.execute(query)).scalars())


async def find_shared_with(db: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID | None,
                           status_filter: CandidateStatus | None) -> list[MatchingShortlisting]:
    """Entries shared with a hiring manager (jobs not deleted), grouped by job, best score first."""
    query = (select(MatchingShortlisting)
             .join(Job, Job.id == MatchingShortlisting.job_id)
             .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
             .where(MatchingShortlisting.shared_to == user_id, Job.deleted_at.is_(None), Candidate.deleted_at.is_(None))
             .order_by(MatchingShortlisting.job_id, MatchingShortlisting.total_score.desc(), MatchingShortlisting.id))
    if job_id:
        query = query.where(MatchingShortlisting.job_id == job_id)
    if status_filter:
        query = query.where(MatchingShortlisting.status == status_filter)
    return list((await db.execute(query)).scalars())


async def find_approved_shared_with(db: AsyncSession, user_id: uuid.UUID,
                                    job_id: uuid.UUID | None) -> list[MatchingShortlisting]:
    query = (select(MatchingShortlisting).join(Job, Job.id == MatchingShortlisting.job_id)
             .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
             .where(MatchingShortlisting.shared_to == user_id,
                    MatchingShortlisting.status == CandidateStatus.APPROVED,
                    Job.deleted_at.is_(None), Candidate.deleted_at.is_(None)))
    if job_id:
        query = query.where(MatchingShortlisting.job_id == job_id)
    return list((await db.execute(query)).scalars())


async def has_shared_entry(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """True when at least one entry of the job was shared with this person."""
    found = (await db.execute(select(MatchingShortlisting.id).where(
        MatchingShortlisting.job_id == job_id, MatchingShortlisting.shared_to == user_id).limit(1)
    )).scalar_one_or_none()
    return found is not None


async def find_review_events(db: AsyncSession, job_id: uuid.UUID, actions: tuple[Action, ...]) -> list[AuditLog]:
    return list((await db.execute(
        select(AuditLog).where(AuditLog.job_id == job_id, AuditLog.actions.in_(actions))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all())
