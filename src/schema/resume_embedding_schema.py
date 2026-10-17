import uuid

from pydantic import BaseModel, Field

from src.constants.enums import EmbeddingKind

EMBEDDING_DIM = 768


class ResumeEmbeddingBase(BaseModel):
    kind: EmbeddingKind
    vector: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    model_version: str | None = None


class ResumeEmbeddingCreate(ResumeEmbeddingBase):
    candidate_id: uuid.UUID
    resume_id: uuid.UUID


class ResumeEmbeddingUpdate(BaseModel):
    vector: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    model_version: str | None = None
