import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.api.rest.schemas.ingestion_schema import BatchEvent, RejectedFile
from src.constants.enums import CandidateStatus


class ShortlistFilters(BaseModel):
    """Filters on the matched candidates (applied after matching, before picking the top K)."""

    category: str | None = Field(default=None, description="e.g. IT, Medical")
    location: str | None = None
    min_experience: float | None = Field(default=None, ge=0)
    max_experience: float | None = Field(default=None, ge=0)
    education: str | None = Field(default=None, description="text found in the degree, e.g. B.Tech")
    must_have_skills: list[str] = Field(default_factory=list)


class ShortlistRunRequest(BaseModel):
    # Candidates the recruiter added by hand from another job; they are scored whatever their category or similarity.
    extra_candidate_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class TopKRequest(BaseModel):
    """Shortlist the best K of the matched candidates that pass these filters."""

    top_k: int = Field(ge=1, le=100)
    filters: ShortlistFilters = Field(default_factory=ShortlistFilters)
    min_score: float | None = Field(default=None, ge=0, le=100)


class TopKResult(BaseModel):
    shortlisted: int
    matching: int


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
    shared_by: uuid.UUID | None = None
    shared_to: uuid.UUID | None = None
    job_title: str | None = None
    # Set once the hiring manager scheduled the interview (the status stays "approved").
    interview_scheduled_at: datetime | None = None
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
    considered: int | None = None  # resumes in the job's category
    pool: int | None = None  # of those, similarity >= threshold (sent to the LLM)
    below_threshold: int | None = None
    not_indexed: int | None = None  # no search index yet (embedding quota)
    threshold: float | None = None
    note: str | None = None
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


class NotesUpdate(BaseModel):
    notes: str | None = Field(default=None, max_length=2000, description="e.g. 'strong backend, overqualified'")


class ShortlistToggle(BaseModel):
    shortlisted: bool


class ShareRequest(BaseModel):
    hiring_manager_id: uuid.UUID
    entry_ids: list[uuid.UUID] | None = Field(
        default=None, description="default: every shortlisted candidate of the job")
    note: str | None = Field(default=None, max_length=1000)


class ShareResult(BaseModel):
    shared: int
    skipped: list[dict] = []


class ScheduleRequest(BaseModel):
    job_id: uuid.UUID | None = Field(default=None, description="only this job; default: all jobs")


class ScheduleResult(BaseModel):
    scheduled: int


class DecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=1000)


class ReviewEvent(BaseModel):
    at: datetime
    action: str | None
    user_id: uuid.UUID | None
    detail: dict | str | None = None
