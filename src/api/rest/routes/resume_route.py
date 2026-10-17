import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser, Role, require_roles
from src.config.settings import settings
from src.constants.enums import CandidateStatus
from src.core.services import candidate_service, resume_service
from src.core.services.candidate_service import SortField
from src.data.clients.postgres import get_db
from src.schema.candidate_schema import (
    CandidateDetail,
    CandidateListResponse,
    CategoryCount,
)
from src.schema.ingestion_schema import BatchStatus, UploadResponse

ResumeRouter = APIRouter(tags=["Resumes"])

# Only recruiters upload and browse the resume pool.
recruiter_only = require_roles(Role.RECRUITER)


@ResumeRouter.post(
    "/resumes/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_resumes(
    files: list[UploadFile] = File(..., description="PDF or DOCX resumes (scanned PDFs are OCR'd)"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(recruiter_only),
):
    """Upload resumes in bulk. Returns a batch id at once; poll /resumes/batches/{id} for progress."""
    if len(files) > settings.max_resumes_per_upload:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"At most {settings.max_resumes_per_upload} files per upload.",
        )
    uploads = [
        resume_service.UploadedFile(filename=f.filename or "resume", data=await f.read())
        for f in files
    ]
    result = await resume_service.accept_uploads(db, user, uploads)
    return UploadResponse(batch_id=result.batch_id, accepted=result.accepted, rejected=result.rejected)


@ResumeRouter.get("/resumes/batches/{batch_id}", response_model=BatchStatus)
async def batch_status(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """Stage (created / collecting / reading / partly_completed / failed), counts and the event log."""
    return await candidate_service.get_batch_status(db, batch_id)


@ResumeRouter.get("/candidates", response_model=CandidateListResponse)
async def list_candidates(
    category: str | None = Query(default=None, description="e.g. IT, Medical"),
    search: str | None = Query(default=None, description="name, email or title"),
    location: str | None = None,
    skill: str | None = None,
    min_experience: float | None = Query(default=None, ge=0),
    max_experience: float | None = Query(default=None, ge=0),
    status_filter: CandidateStatus | None = Query(default=None, alias="status"),
    source_site: str | None = None,
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
        status_filter=status_filter, source_site=source_site, sort=sort,
        descending=(order == "desc"), limit=limit, offset=offset,
    )
    return CandidateListResponse(items=items, total=total)


@ResumeRouter.get("/candidates/categories", response_model=list[CategoryCount])
async def candidate_categories(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    """How many candidates are in each category."""
    return await candidate_service.category_counts(db)


@ResumeRouter.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    return await candidate_service.get_candidate(db, candidate_id)


@ResumeRouter.get("/candidates/{candidate_id}/resume")
async def download_resume(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(recruiter_only),
):
    document, content = await candidate_service.get_resume_file(db, candidate_id)
    filename = (document.original_filename or "resume").replace('"', "")
    media = "application/pdf" if filename.lower().endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
