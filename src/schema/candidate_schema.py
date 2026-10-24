import uuid
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from src.constants.enums import CandidateStatus
from src.schema.base import AuditRead


class CandidateBase(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    contact: str | None = None
    location: str | None = None
    current_title: str | None = None
    category: str | None = Field(default=None, description="IT, Medical, Finance, ...")
    status: CandidateStatus | None = None
    total_years_experience: Decimal | None = Field(
        default=None, ge=0, max_digits=4, decimal_places=1
    )

class CandidateCreate(CandidateBase):
    current_resume_id: uuid.UUID


class CandidateUpdate(CandidateBase):
    current_resume_id: uuid.UUID | None = None


class CandidateRead(CandidateBase, AuditRead):
    current_resume_id: uuid.UUID
    readiness: str = "ready"
    readiness_reason: str | None = None
