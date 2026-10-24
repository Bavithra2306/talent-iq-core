import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser, Role, require_roles
from src.api.rest.file_response import file_response
from src.api.rest.schemas.candidate_schema import (
    CandidateDetail,
    CandidateListResponse,
    CategoryCount,
    ReadinessUpdate,
)
from src.api.rest.schemas.ingestion_schema import (
    BatchStatus,
    ReviewAccepted,
    ReviewFile,
    UploadResponse,
)
from src.core.services import (
    candidate_service,
    resume_review_service,
    resume_service,
    upload_intake,
)
from src.core.services.candidate_service import SortField
from src.data.clients.postgres import get_db

ResumeRouter = APIRouter(tags=["Resumes"])

# Only recruiters upload and browse the resume pool.
recruiter_only = require_roles(Role.RECRUITER)


@ResumeRouter.post(
    "/resumes/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_resumes(
    files: list[UploadFile] = File(
        ..., description="Up to 150 PDF/DOCX resumes (scanned PDFs are OCR'd), or one zip file"
    ),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(recruiter_only),
):
    """Upload resumes: separate files, or one zip. Returns a batch id at once.

    Every file is judged by its content, not its name. Files that are not PDF or DOCX
    are skipped and listed with the reason; poll /resumes/batches/{id} for progress.
    """
    intake = await upload_intake.collect(files)
    result = await resume_service.accept_uploads(
        db, user, intake.uploads, pre_rejected=intake.rejected
    )
    return UploadResponse(batch_id=result.batch_id, accepted=result.accepted, rejected=result.rejected)


@ResumeRouter.get("/resumes/batches/{batch_id}", response_model=BatchStatus)
async def batch_status(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """Stage (created / collecting / reading / partly_completed / failed), counts and the event log."""
    return await candidate_service.get_batch_status(db, batch_id)


@ResumeRouter.get("/resumes/batches/{batch_id}/candidates", response_model=list[CandidateDetail])
async def batch_candidates(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """The resumes this upload or sourcing batch added, with the fields read from each."""
    return await candidate_service.list_batch_candidates(db, batch_id)


@ResumeRouter.get("/resumes/failed", response_model=dict[str, int], summary="How many resumes failed")
async def failed_resumes(db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(recruiter_only)):
    """Resume files that were stored but never read into a candidate (not counting the review queue)."""
    return {"count": await resume_service.count_failed(db)}


@ResumeRouter.post(
    "/resumes/retry-failed", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED,
    summary="Run failed resumes again",
)
async def retry_failed_resumes(db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(recruiter_only)):
    """Puts every failed resume through the pipeline again as one new batch (poll it like an upload)."""
    result = await resume_service.retry_failed(db, user)
    return UploadResponse(batch_id=result.batch_id, accepted=result.accepted, rejected=result.rejected)


@ResumeRouter.get("/candidates", response_model=CandidateListResponse)
async def list_candidates(
    category: str | None = Query(default=None, description="e.g. IT, Medical"),
    search: str | None = Query(default=None, description="name, email or title"),
    location: str | None = None,
    skill: str | None = None,
    min_experience: float | None = Query(default=None, ge=0),
    max_experience: float | None = Query(default=None, ge=0),
    status_filter: str | None = Query(
        default=None, alias="status",
        pattern="^(new|sourced|shortlisted|approved|rejected|interview_scheduled)$",
        description="new = not matched to any job yet; otherwise the status for at least one job"),
    source_site: str | None = None,
    readiness: str = Query(default="ready", pattern="^(ready|needs_review|unprocessable)$",
                           description="ready = usable for matching; needs_review = waiting for the recruiter; unprocessable = set aside"),
    origin: str | None = Query(default=None, pattern="^(uploaded|sourced)$",
                               description="uploaded = added by a recruiter; sourced = found by a sourcing run"),
    sort: SortField = "date_added",
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    items, total = await candidate_service.list_candidates(
        db, category=category, search=search, location=location, skill=skill,
        min_experience=min_experience, max_experience=max_experience,
        status_filter=status_filter, source_site=source_site, readiness=readiness, origin=origin, sort=sort,
        descending=(order == "desc"), limit=limit, offset=offset,
    )
    return CandidateListResponse(items=items, total=total)


@ResumeRouter.get("/candidates/readiness-counts", response_model=dict[str, int], summary="Resumes per readiness")
async def readiness_counts(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """How many resumes are ready, waiting for the recruiter, and unprocessable."""
    return await candidate_service.readiness_counts(db)


@ResumeRouter.put("/candidates/{candidate_id}/readiness", status_code=status.HTTP_204_NO_CONTENT, summary="Place a resume")
async def set_readiness(
    candidate_id: uuid.UUID,
    body: ReadinessUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(recruiter_only),
):
    """The recruiter keeps the resume in the ready list or sets it aside as unprocessable. Nothing is deleted."""
    await candidate_service.set_readiness(db, user, candidate_id, body.readiness)


@ResumeRouter.get("/candidates/categories", response_model=list[CategoryCount])
async def candidate_categories(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """How many candidates are in each category."""
    return await candidate_service.category_counts(db)


@ResumeRouter.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a resume")
async def delete_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(recruiter_only),
):
    """Soft delete: the resume disappears from every list and result; nothing is erased."""
    await candidate_service.delete_candidate(db, user, candidate_id)


@ResumeRouter.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    return await candidate_service.get_candidate(db, candidate_id)


@ResumeRouter.get("/candidates/{candidate_id}/resume", summary="View a resume",
                  description="PDF opens in the browser, Word files as a preview page.")
async def download_resume(
    candidate_id: uuid.UUID,
    download: bool = Query(default=False, description="true: the original file as a download"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    document, content = await candidate_service.get_resume_file(db, candidate_id)
    return file_response(content, document.original_filename or "resume", download)


# ---------- files the AI judged "not a resume" ----------

@ResumeRouter.get(
    "/resumes/review", response_model=list[ReviewFile],
    summary="Files waiting for review",
    description='Uploaded or sourced files the AI judged "not a resume". The recruiter decides.',
)
async def review_files(db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(recruiter_only)):
    return await resume_review_service.list_files(db)


@ResumeRouter.get("/resumes/review/{document_id}/file", summary="Open a file waiting for review")
async def review_file(document_id: uuid.UUID, download: bool = False,
                      db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(recruiter_only)):
    document, content = await resume_review_service.get_file(db, document_id)
    return file_response(content, document.original_filename or "file", download)


@ResumeRouter.post(
    "/resumes/review/{document_id}/accept", response_model=ReviewAccepted,
    summary="It is a resume", description="Reads the file as a resume and saves the candidate.",
)
async def accept_review_file(document_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                             user: CurrentUser = Depends(recruiter_only)):
    return await resume_review_service.accept_as_resume(db, user, document_id)


@ResumeRouter.delete(
    "/resumes/review/{document_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Not a resume: delete it", description="Removes the file and its record.",
)
async def delete_review_file(document_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                             user: CurrentUser = Depends(recruiter_only)):
    await resume_review_service.delete_file(db, user, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
