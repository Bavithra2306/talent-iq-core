import uuid

from pydantic import BaseModel, Field

from src.constants.enums import JobStatus
from src.schema.base import AuditRead


class JobBase(BaseModel):
    title: str = Field(min_length=1)
    category: str | None = None
    industry: str | None = None
    experience_years: int | None = Field(default=None, ge=0)
    education: str | None = None
    location: str | None = None
    work_mode: str | None = Field(default=None, description="onsite / remote / hybrid")
    status: JobStatus = JobStatus.OPEN
    skills: list[str] | None = Field(default=None, description="required skills")
    nice_to_have_skills: list[str] | None = None


class JobCreate(JobBase):
    # Set when the JD came from an uploaded PDF (id from /jobs/extract).
    # Left empty for a form-created JD: a PDF is generated from the fields.
    jd_id: uuid.UUID | None = None


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    industry: str | None = None
    experience_years: int | None = Field(default=None, ge=0)
    education: str | None = None
    location: str | None = None
    work_mode: str | None = None
    status: JobStatus | None = None
    skills: list[str] | None = None
    nice_to_have_skills: list[str] | None = None


class JobRead(JobBase, AuditRead):
    jd_id: uuid.UUID
