import uuid
from datetime import datetime

from pydantic import BaseModel


class AcceptedFile(BaseModel):
    document_id: uuid.UUID
    filename: str


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    batch_id: str | None = None
    accepted: list[AcceptedFile]
    rejected: list[RejectedFile]


class BatchEvent(BaseModel):
    at: datetime
    stage: str | None = None
    action: str | None = None
    detail: str | None = None


class BatchStatus(BaseModel):
    batch_id: str
    stage: str
    finished: bool
    total: int | None = None
    saved: int | None = None
    failed: int | None = None
    seconds: float | None = None
    embeddings_pending: int | None = None
    embeddings_note: str | None = None
    failures: list[RejectedFile] = []
    events: list[BatchEvent]
