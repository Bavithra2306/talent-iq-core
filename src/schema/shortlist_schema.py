import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.constants.enums import CandidateStatus
from src.schema.ingestion_schema import BatchEvent, RejectedFile


class ShortlistFilters(BaseModel):
    """Hard filters that decide which candidates are considered."""

    category: str | None = Field(default=None, description="e.g. IT, Medical")
    location: str | None = None
    min_experience: float | None = Field(default=None, ge=0)
    max_experience: float | None = Field(default=None, ge=0)
    education: str | None = Field(default=None, description="text found in the degree, e.g. B.Tech")
    must_have_skills: list[str] = Field(default_factory=list)


class ShortlistRunRequest(BaseModel):
    filters: ShortlistFilters = Field(default_factory=ShortlistFilters)
    top_k: int = Field(default=10, ge=1, le=100)


class RunAccepted(BaseModel):
    run_id: str


class ParseFilterRequest(BaseModel):
    text: str = Field(min_length=2, max_length=500)


class ParseFilterResponse(BaseModel):
    """The corrected search for the user to confirm, then send back as a run request."""

    original_text: str
    corrected_text: str
    filters: ShortlistFilters
    top_k: int | None = None


class ScoreParts(BaseModel):
    skills: float | None = None
    role: float | None = None
    experience: float | None = None
    education: float | None = None


class ShortlistEntry(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    candidate_id: uuid.UUID
    resume_id: uuid.UUID | None = None
    name: str | None = None
    email: str | None = None
    contact: str | None = None
    location: str | None = None
    current_title: str | None = None
    category: str | None = None
    total_years_experience: float | None = None
    skills: list[str] = []
    status: CandidateStatus
    rank: int | None = None
    total_score: float | None = None
    scores: ScoreParts
    breakdown: dict | None = None
    explanation: dict | None = None
    weights_used: dict | None = None
    notes_by_recruiter: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ShortlistListResponse(BaseModel):
    items: list[ShortlistEntry]
    total: int
    weights: dict


class RunStatus(BaseModel):
    run_id: str
    job_id: uuid.UUID | None = None
    stage: str
    finished: bool
    pool: int | None = None
    scored: int | None = None
    from_cache: int | None = None
    shortlisted: int | None = None
    failed: int | None = None
    seconds: float | None = None
    failures: list[RejectedFile] = []
    events: list[BatchEvent]


class LogEntry(BaseModel):
    id: uuid.UUID
    at: datetime
    user_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    stage: str | None = None
    category: str | None = None
    action: str | None = None
    input_data: str | None = None
    output_data: str | None = None
    duration_ms: int | None = None


class LogListResponse(BaseModel):
    items: list[LogEntry]
    total: int
