import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.constants.enums import Action, ActionCategory, PipelineStage
from src.schema.base import ORMModel


class AuditLogBase(BaseModel):
    user_id: uuid.UUID | None = None
    action_category: ActionCategory | None = None
    actions: Action | None = None
    stage: PipelineStage | None = None
    job_id: uuid.UUID | None = None
    input_data: str | None = None
    output_data: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration: int | None = Field(default=None, ge=0, description="milliseconds")


class AuditLogCreate(AuditLogBase):
    created_by: uuid.UUID | None = None


class AuditLogUpdate(BaseModel):
    output_data: str | None = None
    end_time: datetime | None = None
    duration: int | None = Field(default=None, ge=0)
    updated_by: uuid.UUID | None = None


class AuditLogRead(AuditLogBase, ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
