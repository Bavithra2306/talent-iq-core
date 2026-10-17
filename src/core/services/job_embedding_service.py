"""Embeddings of a job's required skills, role title and nice-to-have skills."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.services import embedding_service
from src.core.services.embedding_service import EmbeddingError
from src.data.models.job import Job
from src.data.models.job_embedding import JobEmbedding

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
        vectors = await embedding_service.embed_texts(list(present.values()))
    except EmbeddingError as exc:
        logger.warning("job %s embedding skipped: %s", job.id, exc)
        return False

    row = (await db.execute(
        select(JobEmbedding).where(JobEmbedding.job_id == job.id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        row = JobEmbedding(job_id=job.id, created_by=user_id)
        db.add(row)
    else:
        row.updated_by = user_id
    for column in texts:
        setattr(row, column, dict(zip(present, vectors)).get(column))
    row.model_version = 1  # 1 = gemini-embedding-001 at 768 dimensions
    await db.commit()
    return True
