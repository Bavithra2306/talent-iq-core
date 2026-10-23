"""Response models for the /candidates routes."""

import uuid

from pydantic import BaseModel

from src.constants.enums import CandidateStatus
from src.schema.candidate_schema import CandidateRead


class JobStatusItem(BaseModel):
    """Where a candidate stands for one job (statuses are per job)."""

    job_id: uuid.UUID
    job_title: str
    status: CandidateStatus
    total_score: float | None = None


class CandidateListItem(CandidateRead):
    skills: list[str] = []
    source_site: str | None = None
    # Empty means "New": not matched to any job yet.
    job_statuses: list[JobStatusItem] = []


class CandidateListResponse(BaseModel):
    items: list[CandidateListItem]
    total: int


class CandidateDetail(CandidateListItem):
    education: list[dict] | dict | None = None
    roles_and_years: list[dict] | dict | None = None
    extraction_model: str | None = None
    file_name: str | None = None


class LocationCount(BaseModel):
    location: str | None
    count: int


class CategoryCount(BaseModel):
    category: str | None
    count: int
