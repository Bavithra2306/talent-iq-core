import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser, Role, require_roles
from src.api.rest.schemas.shortlist_schema import (
    DecisionRequest,
    NotesUpdate,
    ReviewEvent,
    ShareRequest,
    ShareResult,
    ShortlistEntry,
    ShortlistToggle,
)
from src.constants.enums import CandidateStatus
from src.core.services import review_service
from src.data.clients.postgres import get_db
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting

ReviewRouter = APIRouter(tags=["Review"])

recruiter_only = require_roles(Role.RECRUITER)
hiring_manager_only = require_roles(Role.HIRING_MANAGER)
reviewers = require_roles(Role.RECRUITER, Role.HIRING_MANAGER)


# ---------- recruiter ----------

@ReviewRouter.patch("/shortlist-entries/{entry_id}/notes", response_model=ShortlistEntry)
async def update_notes(entry_id: uuid.UUID, body: NotesUpdate, db: AsyncSession = Depends(get_db),
                       user: CurrentUser = Depends(recruiter_only)):
    """Recruiter's note on a candidate (before or after the final shortlist), e.g. 'overqualified'."""
    return await review_service.set_notes(db, user, entry_id, body.notes)


@ReviewRouter.post("/shortlist-entries/{entry_id}/shortlist", response_model=ShortlistEntry)
async def toggle_shortlist(entry_id: uuid.UUID, body: ShortlistToggle, db: AsyncSession = Depends(get_db),
                           user: CurrentUser = Depends(recruiter_only)):
    """Add a candidate to, or remove them from, the final shortlist by hand (before sharing)."""
    return await review_service.set_shortlisted(db, user, entry_id, body.shortlisted)


@ReviewRouter.post("/jobs/{job_id}/shortlist/share", response_model=ShareResult)
async def share_shortlist(job_id: uuid.UUID, body: ShareRequest, db: AsyncSession = Depends(get_db),
                          user: CurrentUser = Depends(recruiter_only),
                          access_token: str = Cookie()):
    """Share shortlisted candidates (with their notes) with a hiring manager.
    Pick the hiring manager from the auth service: GET /api/v1/users/directory?role=hiring_manager."""
    return await review_service.share(db, user, access_token, job_id, body.hiring_manager_id,
                                      body.entry_ids, body.note)


# ---------- hiring manager ----------

@ReviewRouter.get("/review", response_model=list[ShortlistEntry])
async def shared_with_me(job_id: uuid.UUID | None = None,
                         status_filter: CandidateStatus | None = Query(default=None, alias="status"),
                         db: AsyncSession = Depends(get_db),
                         user: CurrentUser = Depends(hiring_manager_only)):
    """Candidates shared with me, with scores, explanation and the recruiter's notes."""
    return await review_service.list_shared(db, user, job_id, status_filter)


@ReviewRouter.get("/review/{entry_id}/resume")
async def shared_resume(entry_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user: CurrentUser = Depends(hiring_manager_only)):
    document, content = await review_service.shared_resume(db, user, entry_id)
    filename = (document.original_filename or "resume").replace('"', "")
    media = "application/pdf" if filename.lower().endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@ReviewRouter.post("/review/{entry_id}/decision", response_model=ShortlistEntry)
async def decide(entry_id: uuid.UUID, body: DecisionRequest, db: AsyncSession = Depends(get_db),
                 user: CurrentUser = Depends(hiring_manager_only)):
    """Approve or reject a shared candidate (can be changed until the interview is scheduled)."""
    return await review_service.decide(db, user, entry_id, body.decision, body.comment)


@ReviewRouter.post("/review/{entry_id}/schedule-interview", response_model=ShortlistEntry)
async def schedule_interview(entry_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                             user: CurrentUser = Depends(hiring_manager_only)):
    """Mark an approved candidate as 'interview scheduled' (status change only)."""
    return await review_service.schedule_interview(db, user, entry_id)


# ---------- both ----------

@ReviewRouter.get("/jobs/{job_id}/history", response_model=list[ReviewEvent])
async def review_history(job_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: CurrentUser = Depends(reviewers)):
    """Timeline of a job's shortlist: shared, notes, approvals, rejections, interviews (oldest first)."""
    if user.role == Role.HIRING_MANAGER:
        shared = (await db.execute(select(MatchingShortlisting.id).where(
            MatchingShortlisting.job_id == job_id, MatchingShortlisting.shared_to == user.id).limit(1)
        )).scalar_one_or_none()
        if shared is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing was shared with you for this job.")
    return await review_service.history(db, user, job_id)
