import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AuditRead(ORMModel):
    """Audit fields returned on tables that carry the standard audit columns."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
