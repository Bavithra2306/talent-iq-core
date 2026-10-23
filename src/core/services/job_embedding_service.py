"""Embeddings of a job's required skills, role title and nice-to-have skills."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.job import Job
from src.data.models.postgres.job_embedding import JobEmbedding
from src.data.repositories import job_embedding_repository
from src.handlers.http_clients import embedding_client
from src.handlers.http_clients.embedding_client import EmbeddingError

logger = logging.getLogger("talentiq.job_embeddings")


async def refresh_job_embedding(db: AsyncSession, job: Job, user_id: uuid.UUID | None) -> bool:
    """(Re)compute and store the job's vectors. Never raises: a failure is logged and the
    job stays usable; the embedding is retried the next time the job is edited."""
    texts = {
        "skill_vector": ", ".join(job.skills or []),
        "role_vector": job.title or "",
        "nice_to_have_skill_vector": ", ".join(job.nice_to_have_skills or []),
    }
    present = {k: v for k, v in texts.items() if v.strip()}
    try:
        vectors = await embedding_client.embed_texts(list(present.values()))
    except EmbeddingError as exc:
        logger.warning("job %s embedding skipped: %s", job.id, exc)
        return False

    row = await job_embedding_repository.find_by_job(db, job.id)
    if row is None:
        row = JobEmbedding(job_id=job.id, created_by=user_id)
        job_embedding_repository.add(db, row)
    else:
        row.updated_by = user_id
    for column in texts:
        setattr(row, column, dict(zip(present, vectors, strict=False)).get(column))
    row.model_version = 1  # 1 = gemini-embedding-001 at 768 dimensions
    await db.commit()
    return True


async def find_embedding(db: AsyncSession, job_id: uuid.UUID) -> JobEmbedding | None:
    """The stored vectors of a job, if any."""
    return await job_embedding_repository.find_by_job(db, job_id)
