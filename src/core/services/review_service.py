"""Review between recruiter and hiring manager.

Recruiter: notes on any scored candidate, add/remove candidates from the final
shortlist, share the shortlist with a hiring manager.
Hiring manager: sees only what was shared with them, approves / rejects (with an
optional comment) and marks approved candidates as "interview scheduled" (a status
change only). The recruiter sees every decision straight away.

Statuses on matching_shortlisting:
    sourced <-> shortlisted -> (shared) -> approved / rejected -> interview_scheduled
Every action is written to audit_logs, which is also the review history.
"""

import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser
from src.constants.enums import Action, ActionCategory, CandidateStatus
from src.core.services import matching_service
from src.core.services.audit_service import log_event
from src.core.services.shortlist_service import _entry
from src.core.services.storage_service import storage
from src.core.services.user_directory import find_user
from src.data.models.audit_log import AuditLog
from src.data.models.candidate import Candidate
from src.data.models.document import Document
from src.data.models.job import Job
from src.data.models.matching_shortlisting import MatchingShortlisting
from src.data.models.resume_extraction import ResumeExtraction
from src.schema.shortlist_schema import ReviewEvent, ShareResult, ShortlistEntry

REVIEW_ACTIONS = (
    Action.CANDIDATE_ADDED_TO_SHORTLIST, Action.CANDIDATE_REMOVED_FROM_SHORTLIST,
    Action.NOTES_UPDATED, Action.SHORTLIST_SHARED, Action.CANDIDATE_APPROVED,
    Action.CANDIDATE_REJECTED, Action.INTERVIEW_SCHEDULED, Action.SHORTLIST_RETURNED,
)
DECIDED = (CandidateStatus.APPROVED, CandidateStatus.REJECTED, CandidateStatus.INTERVIEW_SCHEDULED)


# ---------- helpers ----------

async def _row(db: AsyncSession, entry_id: uuid.UUID) -> MatchingShortlisting:
    row = await db.get(MatchingShortlisting, entry_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shortlist entry not found.")
    return row


async def _hm_row(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> MatchingShortlisting:
    """A row shared with this hiring manager (others get 404, not 403, to avoid leaking ids)."""
    row = await db.get(MatchingShortlisting, entry_id)
    if row is None or row.shared_to != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shortlist entry not found.")
    return row


async def to_entry(db: AsyncSession, row: MatchingShortlisting) -> ShortlistEntry:
    # updated_at is set by the database on update; reload it before reading.
    await db.refresh(row)
    candidate = await db.get(Candidate, row.candidate_id)
    skills = (await db.execute(
        select(ResumeExtraction.skills).where(ResumeExtraction.resume_id == row.resume_id).limit(1)
    )).scalar_one_or_none()
    weights = await matching_service.load_weights(db, row.job_id)
    entry = _entry(row, candidate, skills, weights, row.rank,
                   float(row.total_score) if row.total_score is not None else None)
    job = await db.get(Job, row.job_id)
    entry.job_title = job.title if job else None
    return entry


async def _log(user: CurrentUser, action: Action, row_or_job_id, **detail) -> None:
    job_id = row_or_job_id.job_id if isinstance(row_or_job_id, MatchingShortlisting) else row_or_job_id
    if isinstance(row_or_job_id, MatchingShortlisting):
        detail = {"entry_id": str(row_or_job_id.id),
                  "candidate_id": str(row_or_job_id.candidate_id), **detail}
    await log_event(user_id=user.id, category=ActionCategory.SHORTLISTING, action=action,
                    job_id=job_id, input_data=detail)


async def _sync_candidate_status(db: AsyncSession, row: MatchingShortlisting) -> None:
    candidate = await db.get(Candidate, row.candidate_id)
    if candidate is not None:
        candidate.status = row.status


# ---------- recruiter ----------

async def set_notes(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID, notes: str | None) -> ShortlistEntry:
    row = await _row(db, entry_id)
    row.notes_by_recruiter = (notes or "").strip() or None
    row.updated_by = user.id
    await db.commit()
    await _log(user, Action.NOTES_UPDATED, row, notes=row.notes_by_recruiter)
    return await to_entry(db, row)


async def set_shortlisted(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID,
                          shortlisted: bool) -> ShortlistEntry:
    """Recruiter's manual change to the final shortlist (before it is shared)."""
    row = await _row(db, entry_id)
    if row.shared_to is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This candidate was already shared with a hiring manager.")
    if row.status not in (CandidateStatus.SOURCED, CandidateStatus.SHORTLISTED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"The candidate is already {row.status.value}.")
    row.status = CandidateStatus.SHORTLISTED if shortlisted else CandidateStatus.SOURCED
    row.updated_by = user.id
    await db.commit()
    await _log(user, Action.CANDIDATE_ADDED_TO_SHORTLIST if shortlisted
               else Action.CANDIDATE_REMOVED_FROM_SHORTLIST, row)
    return await to_entry(db, row)


async def share(db: AsyncSession, user: CurrentUser, access_token: str, job_id: uuid.UUID,
                hiring_manager_id: uuid.UUID, entry_ids: list[uuid.UUID] | None,
                note: str | None) -> ShareResult:
    if await db.get(Job, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    manager = await find_user(access_token, hiring_manager_id, "hiring_manager")
    if manager is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "That user is not an active hiring manager.")

    query = select(MatchingShortlisting).where(MatchingShortlisting.job_id == job_id)
    if entry_ids:
        query = query.where(MatchingShortlisting.id.in_(entry_ids))
    else:
        query = query.where(MatchingShortlisting.status == CandidateStatus.SHORTLISTED)
    rows = list((await db.execute(query)).scalars())
    if entry_ids and len(rows) != len(set(entry_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Some entries don't belong to this job.")
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "There are no shortlisted candidates to share.")

    shared, skipped = 0, []
    for row in rows:
        if row.status != CandidateStatus.SHORTLISTED:
            skipped.append({"entry_id": str(row.id), "reason": f"status is {row.status.value}"})
        elif row.shared_to not in (None, hiring_manager_id):
            skipped.append({"entry_id": str(row.id), "reason": "already shared with another hiring manager"})
        else:
            row.shared_by, row.shared_to, row.updated_by = user.id, hiring_manager_id, user.id
            shared += 1
    await db.commit()
    await _log(user, Action.SHORTLIST_SHARED, job_id, hiring_manager_id=str(hiring_manager_id),
               hiring_manager=manager.get("email"), shared=shared, skipped=len(skipped), note=note)
    return ShareResult(shared=shared, skipped=skipped)


# ---------- hiring manager ----------

async def list_shared(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID | None,
                      status_filter: CandidateStatus | None) -> list[ShortlistEntry]:
    query = (select(MatchingShortlisting).where(MatchingShortlisting.shared_to == user.id)
             .order_by(MatchingShortlisting.job_id, MatchingShortlisting.total_score.desc()))
    if job_id:
        query = query.where(MatchingShortlisting.job_id == job_id)
    if status_filter:
        query = query.where(MatchingShortlisting.status == status_filter)
    return [await to_entry(db, row) for row in (await db.execute(query)).scalars()]


async def shared_resume(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> tuple[Document, bytes]:
    row = await _hm_row(db, user, entry_id)
    document = await db.get(Document, row.resume_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume file not found.")
    return document, storage.read(document.file_path)


async def decide(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID, decision: str,
                 comment: str | None) -> ShortlistEntry:
    row = await _hm_row(db, user, entry_id)
    if row.status == CandidateStatus.INTERVIEW_SCHEDULED:
        raise HTTPException(status.HTTP_409_CONFLICT, "The interview is already scheduled.")
    row.status = CandidateStatus.APPROVED if decision == "approved" else CandidateStatus.REJECTED
    row.updated_by = user.id
    await _sync_candidate_status(db, row)
    await db.commit()
    await _log(user, Action.CANDIDATE_APPROVED if decision == "approved" else Action.CANDIDATE_REJECTED,
               row, comment=(comment or "").strip() or None)
    return await to_entry(db, row)


async def schedule_interview(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> ShortlistEntry:
    row = await _hm_row(db, user, entry_id)
    if row.status != CandidateStatus.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only approved candidates can be scheduled.")
    row.status = CandidateStatus.INTERVIEW_SCHEDULED
    row.updated_by = user.id
    await _sync_candidate_status(db, row)
    await db.commit()
    await _log(user, Action.INTERVIEW_SCHEDULED, row)
    return await to_entry(db, row)


# ---------- history ----------

async def history(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> list[ReviewEvent]:
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.job_id == job_id, AuditLog.actions.in_(REVIEW_ACTIONS))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all()
    events = []
    for r in rows:
        try:
            detail = json.loads(r.input_data) if r.input_data else None
        except ValueError:
            detail = r.input_data
        events.append(ReviewEvent(at=r.created_at, action=r.actions.value, user_id=r.user_id, detail=detail))
    return events
