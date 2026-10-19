import uuid

from pydantic import BaseModel, Field

EMBEDDING_DIM = 768


class JobEmbeddingBase(BaseModel):
    skill_vector: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    role_vector: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    nice_to_have_skill_vector: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    model_version: int | None = None


class JobEmbeddingCreate(JobEmbeddingBase):
    job_id: uuid.UUID


class JobEmbeddingUpdate(JobEmbeddingBase):
    pass
