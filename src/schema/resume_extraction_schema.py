import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from src.schema.base import AuditRead


class ResumeExtractionBase(BaseModel):
    skills: list[str] | None = None
    total_experience_years: Decimal | None = Field(
        default=None, ge=0, max_digits=4, decimal_places=1
    )
    education: list[dict] | dict | None = None
    current_role: str | None = None
    roles_and_years: list[dict] | dict | None = None
    model_version: str | None = None


class ResumeExtractionCreate(ResumeExtractionBase):
    resume_id: uuid.UUID


class ResumeExtractionUpdate(ResumeExtractionBase):
    pass


class ResumeExtractionRead(ResumeExtractionBase, AuditRead):
    resume_id: uuid.UUID
