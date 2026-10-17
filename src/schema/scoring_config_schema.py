import uuid
from typing import Self

from pydantic import BaseModel, Field, model_validator

from src.schema.base import AuditRead


class ScoringConfigBase(BaseModel):
    skill_pct: int = Field(default=40, ge=0, le=100)
    role_pct: int = Field(default=20, ge=0, le=100)
    exp_pct: int = Field(default=25, ge=0, le=100)
    edu_pct: int = Field(default=15, ge=0, le=100)
    total_pct: int = 100

    @model_validator(mode="after")
    def weights_sum_to_100(self) -> Self:
        total = self.skill_pct + self.role_pct + self.exp_pct + self.edu_pct
        if total != 100 or self.total_pct != 100:
            raise ValueError("skill, role, exp and edu percentages must sum to 100")
        return self


class ScoringConfigCreate(ScoringConfigBase):
    job_id: uuid.UUID


class ScoringConfigUpdate(ScoringConfigBase):
    """Weights are updated together so the 100% total always holds."""


class ScoringConfigRead(ScoringConfigBase, AuditRead):
    job_id: uuid.UUID
