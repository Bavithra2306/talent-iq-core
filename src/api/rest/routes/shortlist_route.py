import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser, Role, require_roles
from src.constants.enums import CandidateStatus, PipelineStage
from src.core.services import export_service, matching_service, shortlist_service
from src.core.services.scoring_rules import Weights
from src.core.services.shortlist_service import SortField
from src.data.clients.postgres import get_db
from src.schema.scoring_config_schema import ScoringConfigRead, ScoringConfigUpdate
from src.schema.shortlist_schema import (
    LogListResponse,
    ParseFilterRequest,
    ParseFilterResponse,
    RunAccepted,
    RunStatus,
    ShortlistListResponse,
    ShortlistRunRequest,
)

ShortlistRouter = APIRouter(tags=["Shortlisting"])

# Recruiters run matching and tune it; hiring managers/admins can read logs.
recruiter_only = require_roles(Role.RECRUITER)
log_readers = require_roles(Role.RECRUITER, Role.HIRING_MANAGER, Role.ADMIN)


def _preview_weights(skill_pct, role_pct, exp_pct, edu_pct) -> Weights | None:
    given = [skill_pct, role_pct, exp_pct, edu_pct]
    if all(v is None for v in given):
        return None
    if any(v is None for v in given):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Give all four weights (skill_pct, role_pct, exp_pct, edu_pct) to preview.")
    weights = Weights(skill_pct, role_pct, exp_pct, edu_pct)
    try:
        weights.validate()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return weights


@ShortlistRouter.post("/shortlists/parse-filter", response_model=ParseFilterResponse)
async def parse_filter(body: ParseFilterRequest, _: CurrentUser = Depends(recruiter_only)):
    """Turn text like "5yrs experince in chenai" into corrected, structured filters.
    Show `corrected_text` to the user; if they confirm, send `filters` to the run endpoint."""
    return await shortlist_service.parse_filter(body.text)


@ShortlistRouter.post("/jobs/{job_id}/shortlist/run", response_model=RunAccepted,
                      status_code=status.HTTP_202_ACCEPTED)
async def run_shortlist(
    job_id: uuid.UUID, body: ShortlistRunRequest,
    db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(recruiter_only),
):
    """Match the job against the candidate pool (filters + top_k). Poll /shortlists/runs/{run_id}."""
    return RunAccepted(run_id=await matching_service.start_run(db, user, job_id, body))


@ShortlistRouter.get("/shortlists/runs/{run_id}", response_model=RunStatus)
async def run_status(run_id: str, db: AsyncSession = Depends(get_db),
                     _: CurrentUser = Depends(recruiter_only)):
    """Stage (created / collecting / reading / scoring / shortlisted / partly_completed / failed)."""
    return await shortlist_service.get_run_status(db, run_id)


def _list_params(
    status_filter: CandidateStatus | None = Query(default=None, alias="status"),
    min_score: float | None = Query(default=None, ge=0, le=100),
    max_score: float | None = Query(default=None, ge=0, le=100),
    skill: str | None = None, location: str | None = None, category: str | None = None,
    min_experience: float | None = Query(default=None, ge=0),
    max_experience: float | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, description="name, email or title"),
    include_unranked: bool = Query(default=False, description="also show earlier-scored candidates outside the latest run"),
    skill_pct: int | None = Query(default=None, ge=0, le=100, description="preview weights (all four required)"),
    role_pct: int | None = Query(default=None, ge=0, le=100),
    exp_pct: int | None = Query(default=None, ge=0, le=100),
    edu_pct: int | None = Query(default=None, ge=0, le=100),
    sort: SortField = "score", order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=20, ge=1, le=200), offset: int = Query(default=0, ge=0),
) -> dict:
    return dict(
        status_filter=status_filter, min_score=min_score, max_score=max_score, skill=skill,
        location=location, category=category, min_experience=min_experience,
        max_experience=max_experience, search=search, include_unranked=include_unranked,
        preview_weights=_preview_weights(skill_pct, role_pct, exp_pct, edu_pct),
        sort=sort, descending=(order == "desc"), limit=limit, offset=offset,
    )


@ShortlistRouter.get("/jobs/{job_id}/shortlist", response_model=ShortlistListResponse)
async def job_shortlist(
    job_id: uuid.UUID, params: dict = Depends(_list_params),
    db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(recruiter_only),
):
    """Scored candidates of a job. Add all four *_pct params to preview other weights without saving."""
    items, total, weights = await shortlist_service.list_entries(db, job_id=job_id, **params)
    return ShortlistListResponse(items=items, total=total, weights=weights.as_dict())


@ShortlistRouter.get("/shortlists", response_model=ShortlistListResponse)
async def all_shortlists(
    job_id: uuid.UUID | None = None, params: dict = Depends(_list_params),
    db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(recruiter_only),
):
    """Dashboard: scored candidates across all jobs (filter by job_id, status, score, skills, ...)."""
    items, total, weights = await shortlist_service.list_entries(db, job_id=job_id, **params)
    return ShortlistListResponse(items=items, total=total, weights=weights.as_dict())


@ShortlistRouter.get("/jobs/{job_id}/scoring-config", response_model=ScoringConfigRead)
async def get_scoring_config(job_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                             _: CurrentUser = Depends(recruiter_only)):
    from sqlalchemy import select

    from src.data.models.scoring_config import ScoringConfig

    config = (await db.execute(select(ScoringConfig).where(ScoringConfig.job_id == job_id))).scalar_one_or_none()
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scoring config not found.")
    return config


@ShortlistRouter.put("/jobs/{job_id}/scoring-config", response_model=ShortlistListResponse)
async def update_weights(
    job_id: uuid.UUID, body: ScoringConfigUpdate,
    db: AsyncSession = Depends(get_db), user: CurrentUser = Depends(recruiter_only),
):
    """Save new weights (must add up to 100) and re-rank the shortlist instantly from stored scores."""
    weights = await shortlist_service.apply_weights(
        db, user, job_id, Weights(body.skill_pct, body.role_pct, body.exp_pct, body.edu_pct))
    items, total, _ = await shortlist_service.list_entries(db, job_id=job_id)
    return ShortlistListResponse(items=items, total=total, weights=weights.as_dict())


@ShortlistRouter.get("/logs", response_model=LogListResponse)
async def logs(
    job_id: uuid.UUID | None = None, stage: PipelineStage | None = None,
    category: str | None = Query(default=None, description="e.g. extraction, scoring, shortlisting"),
    since: datetime | None = None, until: datetime | None = None,
    only_stages: bool = Query(default=True, description="only stage changes"),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(log_readers),
):
    """Server log: every stage change (created, collecting, reading, scoring, shortlisted, ...) with time."""
    items, total = await shortlist_service.list_logs(
        db, job_id=job_id, stage=stage, category=category, since=since, until=until,
        only_stages=only_stages, limit=limit, offset=offset)
    return LogListResponse(items=items, total=total)


@ShortlistRouter.get("/jobs/{job_id}/shortlist/export")
async def export_shortlist(
    job_id: uuid.UUID,
    format: export_service.ExportFormat = "xlsx",
    scope: export_service.ExportScope = Query(
        default="shortlisted",
        description="shortlisted: final list incl. review outcomes; ranked: latest run; all: every scored candidate"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_roles(Role.RECRUITER, Role.HIRING_MANAGER)),
):
    """Download the shortlist as Excel or CSV (hiring managers get only what was shared with them)."""
    content, media, filename = await export_service.export_shortlist(db, user, job_id, format, scope)
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
