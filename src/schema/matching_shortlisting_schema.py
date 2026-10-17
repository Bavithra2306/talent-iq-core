import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from src.constants.enums import CandidateStatus
from src.schema.base import AuditRead


class MatchingShortlistingBase(BaseModel):
    resume_id: uuid.UUID | None = Field(
        default=None, description="resume version that was scored"
    )
    status: CandidateStatus = CandidateStatus.SOURCED
    llm_skill_score: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    llm_role_score: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    edu_score: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    exp_score: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    total_score: Decimal | None = Field(
        default=None, ge=0, le=100, decimal_places=2, description="out of 100"
    )
    explanation: dict | None = Field(default=None, description="skill and role reasoning")
    weights_used: dict | None = None
    rank: int | None = Field(default=None, ge=1)
    notes_by_recruiter: str | None = None
    shared_by: uuid.UUID | None = None
    shared_to: uuid.UUID | None = None


class MatchingShortlistingCreate(MatchingShortlistingBase):
    job_id: uuid.UUID
    candidate_id: uuid.UUID


class MatchingShortlistingUpdate(MatchingShortlistingBase):
    status: CandidateStatus | None = None


class MatchingShortlistingRead(MatchingShortlistingBase, AuditRead):
    job_id: uuid.UUID
    candidate_id: uuid.UUID
