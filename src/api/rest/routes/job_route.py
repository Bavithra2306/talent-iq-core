import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import (
    CurrentUser,
    Role,
    get_current_user,
    require_roles,
)
from src.api.rest.file_response import file_response
from src.api.rest.schemas.job_schema import JobExtractResponse, JobListResponse
from src.constants.enums import JobStatus
from src.core.services import jd_service
from src.data.clients.postgres import get_db
from src.schema.job_schema import JobCreate, JobRead, JobUpdate

JobRouter = APIRouter(prefix="/jobs", tags=["Jobs"])

# Recruiters and hiring managers can create and edit JDs.
can_manage_jobs = require_roles(Role.RECRUITER, Role.HIRING_MANAGER)


@JobRouter.post("/extract", response_model=JobExtractResponse)
async def extract_jd(
    file: UploadFile = File(..., description="JD as PDF, scanned PDF, DOCX, JPG or PNG (max 10 MB)"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(can_manage_jobs),
):
    """Upload a JD file and get the extracted fields to review. Nothing is saved as a job yet."""
    data = await file.read()
    return await jd_service.extract_jd(db, user, file.filename or "jd.pdf", data)


@JobRouter.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobCreate,
    confirm_duplicate: bool = Query(default=False, description="save even if a job with this title exists"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(can_manage_jobs),
):
    """Save a job: the reviewed draft (with jd_id from /extract) or a form-typed JD (no jd_id).

    A file already used by an active job is refused. A job with the same title and category
    as an active one returns 409 code "duplicate_job" unless confirm_duplicate=true.
    """
    return await jd_service.create_job(db, user, data, confirm_duplicate)


@JobRouter.get("", response_model=JobListResponse)
async def list_jobs(
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    category: str | None = None,
    search: str | None = Query(default=None, description="matches title, role, location or a required skill"),
    location: str | None = None,
    min_experience: int | None = Query(default=None, ge=0),
    max_experience: int | None = Query(default=None, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    items, total = await jd_service.list_jobs(
        db, status_filter=status_filter, category=category, search=search, limit=limit, offset=offset,
        location=location, min_experience=min_experience, max_experience=max_experience,
    )
    return JobListResponse(items=items, total=total)


@JobRouter.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    return await jd_service.get_job(db, job_id)


@JobRouter.patch("/{job_id}", response_model=JobRead)
async def update_job(
    job_id: uuid.UUID,
    data: JobUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(can_manage_jobs),
):
    """Correct any extracted field or close the job."""
    return await jd_service.update_job(db, user, job_id, data)


@JobRouter.get("/{job_id}/view", summary="View the job description",
               description="PDF and images open in the browser, Word files as a preview page.")
async def view_jd(
    job_id: uuid.UUID,
    download: bool = Query(default=False, description="true: the original file as a download"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    document, content = await jd_service.get_jd_file(db, job_id)
    return file_response(content, document.original_filename or "job-description", download)


@JobRouter.delete(
    "/{job_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job",
    description="Soft delete: the job disappears from the app, but stays in the database with its "
                "file, match results and history.",
)
async def delete_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(can_manage_jobs),
):
    await jd_service.delete_job(db, user, job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
