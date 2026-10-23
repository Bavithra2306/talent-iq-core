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
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser, Role
from src.api.rest.schemas.shortlist_schema import ReviewEvent, ShareResult, ShortlistEntry
from src.constants.enums import Action, ActionCategory, CandidateStatus
from src.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from src.core.services import matching_service
from src.core.services.audit_service import log_event
from src.core.services.shortlist_service import _entry
from src.data.clients.storage import storage
from src.data.models.postgres.document import Document
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.repositories import review_repository
from src.handlers.http_clients.auth_client import find_user

REVIEW_ACTIONS = (
    Action.CANDIDATE_ADDED_TO_SHORTLIST, Action.CANDIDATE_REMOVED_FROM_SHORTLIST,
    Action.NOTES_UPDATED, Action.SHORTLIST_SHARED, Action.CANDIDATE_APPROVED,
    Action.CANDIDATE_REJECTED, Action.INTERVIEW_SCHEDULED, Action.SHORTLIST_RETURNED,
)
DECIDED = (CandidateStatus.APPROVED, CandidateStatus.REJECTED, CandidateStatus.INTERVIEW_SCHEDULED)


# ---------- helpers ----------

async def _scheduled_at(db: AsyncSession, entry_ids: list[uuid.UUID]) -> dict[str, datetime]:
    """When each entry's interview was scheduled (kept in the audit log; status stays approved)."""
    if not entry_ids:
        return {}
    rows = await review_repository.find_interview_events(db)
    wanted = {str(i) for i in entry_ids}
    found: dict[str, datetime] = {}
    for data, at in rows:
        try:
            entry_id = json.loads(data or "{}").get("entry_id")
        except ValueError:
            continue
        if entry_id in wanted:
            found[entry_id] = at
    return found

async def _row(db: AsyncSession, entry_id: uuid.UUID) -> MatchingShortlisting:
    row = await review_repository.get_entry(db, entry_id)
    if row is None:
        raise NotFoundError("Shortlist entry not found.")
    return row


async def _hm_row(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> MatchingShortlisting:
    """A row shared with this hiring manager (others get 404, not 403, to avoid leaking ids)."""
    row = await review_repository.get_entry(db, entry_id)
    if row is None or row.shared_to != user.id:
        raise NotFoundError("Shortlist entry not found.")
    return row


async def to_entry(db: AsyncSession, row: MatchingShortlisting) -> ShortlistEntry:
    # updated_at is set by the database on update; reload it before reading.
    await review_repository.refresh(db, row)
    candidate = await review_repository.get_candidate(db, row.candidate_id)
    skills = await review_repository.get_resume_skills(db, row.resume_id)
    weights = await matching_service.load_weights(db, row.job_id)
    entry = _entry(row, candidate, skills, weights, row.rank,
                   float(row.total_score) if row.total_score is not None else None)
    job = await review_repository.get_job(db, row.job_id)
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
    candidate = await review_repository.get_candidate(db, row.candidate_id)
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
        raise ConflictError("This candidate was already shared with a hiring manager.")
    if row.status not in (CandidateStatus.SOURCED, CandidateStatus.SHORTLISTED):
        raise ConflictError(f"The candidate is already {row.status.value}.")
    row.status = CandidateStatus.SHORTLISTED if shortlisted else CandidateStatus.SOURCED
    row.updated_by = user.id
    await db.commit()
    await _log(user, Action.CANDIDATE_ADDED_TO_SHORTLIST if shortlisted
               else Action.CANDIDATE_REMOVED_FROM_SHORTLIST, row)
    return await to_entry(db, row)


async def share(db: AsyncSession, user: CurrentUser, access_token: str, job_id: uuid.UUID,
                hiring_manager_id: uuid.UUID, entry_ids: list[uuid.UUID] | None,
                note: str | None) -> ShareResult:
    job = await review_repository.get_job(db, job_id)
    if job is None or job.deleted_at is not None:
        raise NotFoundError("Job not found.")
    manager = await find_user(access_token, hiring_manager_id, "hiring_manager")
    if manager is None:
        raise UnprocessableError("That user is not an active hiring manager.")

    rows = await review_repository.find_entries_to_share(db, job_id, entry_ids)
    if entry_ids and len(rows) != len(set(entry_ids)):
        raise NotFoundError("Some entries don't belong to this job.")
    if not rows:
        raise ConflictError("There are no shortlisted candidates to share.")

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
    rows = await review_repository.find_shared_with(db, user.id, job_id, status_filter)
    scheduled = await _scheduled_at(db, [r.id for r in rows])
    entries = []
    for row in rows:
        entry = await to_entry(db, row)
        entry.interview_scheduled_at = scheduled.get(str(row.id))
        entries.append(entry)
    return entries


async def shared_resume(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> tuple[Document, bytes]:
    row = await _hm_row(db, user, entry_id)
    document = await review_repository.get_document(db, row.resume_id)
    if document is None:
        raise NotFoundError("Resume file not found.")
    return document, storage.read(document.file_path)


async def decide(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID, decision: str,
                 comment: str | None) -> ShortlistEntry:
    row = await _hm_row(db, user, entry_id)
    if row.status == CandidateStatus.INTERVIEW_SCHEDULED or await _scheduled_at(db, [row.id]):
        raise ConflictError("The interview is already scheduled, so the decision can't change.")
    row.status = CandidateStatus.APPROVED if decision == "approved" else CandidateStatus.REJECTED
    row.updated_by = user.id
    await _sync_candidate_status(db, row)
    await db.commit()
    await _log(user, Action.CANDIDATE_APPROVED if decision == "approved" else Action.CANDIDATE_REJECTED,
               row, comment=(comment or "").strip() or None)
    return await to_entry(db, row)


async def schedule_interviews(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID | None) -> int:
    """One click: schedule interviews for every approved candidate shared with this hiring
    manager (optionally one job) that isn't scheduled yet. The status stays "approved";
    the scheduling is recorded in the history."""
    rows = await review_repository.find_approved_shared_with(db, user.id, job_id)
    already = await _scheduled_at(db, [r.id for r in rows])
    todo = [r for r in rows if str(r.id) not in already]
    if not todo:
        raise ConflictError("There are no approved candidates waiting for an interview.")
    for row in todo:
        await _log(user, Action.INTERVIEW_SCHEDULED, row, scheduled_together=len(todo))
    return len(todo)


async def schedule_interview(db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID) -> ShortlistEntry:
    """Schedule one approved candidate (the status stays "approved")."""
    row = await _hm_row(db, user, entry_id)
    if row.status != CandidateStatus.APPROVED:
        raise ConflictError("Only approved candidates can be scheduled.")
    if await _scheduled_at(db, [row.id]):
        raise ConflictError("The interview is already scheduled.")
    await _log(user, Action.INTERVIEW_SCHEDULED, row)
    entry = await to_entry(db, row)
    entry.interview_scheduled_at = (await _scheduled_at(db, [row.id])).get(str(row.id))
    return entry


# ---------- history ----------

async def history(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> list[ReviewEvent]:
    if user.role == Role.HIRING_MANAGER and not await review_repository.has_shared_entry(db, job_id, user.id):
        raise NotFoundError("Nothing was shared with you for this job.")
    rows = await review_repository.find_review_events(db, job_id, REVIEW_ACTIONS)
    events = []
    for r in rows:
        try:
            detail = json.loads(r.input_data) if r.input_data else None
        except ValueError:
            detail = r.input_data
        events.append(ReviewEvent(at=r.created_at, action=r.actions.value, user_id=r.user_id, detail=detail))
    return events
