"""Request/response models for the /jobs routes."""

import uuid

from pydantic import BaseModel, Field

from src.schema.job_schema import JobRead


class JobDraft(BaseModel):
    """Fields extracted by the LLM from a JD; every field may be missing."""

    title: str | None = None
    role_name: str | None = None
    category: str | None = None
    industry: str | None = None
    experience_years: int | None = Field(default=None, ge=0)
    education: str | None = None
    location: str | None = None
    work_mode: str | None = None
    skills: list[str] = []
    nice_to_have_skills: list[str] = []


class JobExtractResponse(BaseModel):
    """Returned by /jobs/extract for the recruiter to review before saving."""

    document_id: uuid.UUID
    draft: JobDraft
    existing_job_id: uuid.UUID | None = None
    warnings: list[str] = []


class JobListResponse(BaseModel):
    items: list[JobRead]
    total: int
