"""Database access for matching: the candidate pool, stored scores and the result rows."""

import uuid

from sqlalchemy import Float, and_, cast, null, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.constants.enums import EmbeddingKind
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_embedding import ResumeEmbedding
from src.data.models.postgres.resume_extraction import ResumeExtraction


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await db.get(Job, job_id)


def _similarity_query(job_vectors: tuple[list[float] | None, list[float] | None]):
    """Candidates with their extracted resume and the cosine similarity of their skills and role vectors to the job."""
    skill_vec, role_vec = job_vectors
    skill_emb, role_emb = aliased(ResumeEmbedding), aliased(ResumeEmbedding)
    sim_skill = (1 - skill_emb.vector.cosine_distance(skill_vec)) if skill_vec else cast(null(), Float)
    sim_role = (1 - role_emb.vector.cosine_distance(role_vec)) if role_vec else cast(null(), Float)
    return (
        select(Candidate, ResumeExtraction, sim_skill.label("sim_skill"), sim_role.label("sim_role"))
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .outerjoin(skill_emb, and_(skill_emb.resume_id == Candidate.current_resume_id,
                                   skill_emb.kind == EmbeddingKind.SKILL))
        .outerjoin(role_emb, and_(role_emb.resume_id == Candidate.current_resume_id,
                                  role_emb.kind == EmbeddingKind.ROLE))
    )


async def find_pool(db: AsyncSession, job_vectors: tuple[list[float] | None, list[float] | None],
                    category: str | None) -> list[tuple]:
    """(candidate, extraction, skill similarity, role similarity) for every resume in the category."""
    query = _similarity_query(job_vectors)
    if category:
        query = query.where(Candidate.category == category)
    return [tuple(r) for r in (await db.execute(query)).all()]


async def find_pool_by_candidates(db: AsyncSession, job_vectors: tuple[list[float] | None, list[float] | None],
                                  candidate_ids: list[uuid.UUID]) -> list[tuple]:
    """The same rows for chosen candidates, whatever their category."""
    query = _similarity_query(job_vectors).where(Candidate.id.in_(candidate_ids))
    return [tuple(r) for r in (await db.execute(query)).all()]


async def find_rows_by_candidates(db: AsyncSession, job_id: uuid.UUID,
                                  candidate_ids: list[uuid.UUID]) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting).where(
            MatchingShortlisting.job_id == job_id,
            MatchingShortlisting.candidate_id.in_(candidate_ids),
        )
    )).scalars().all())


async def find_rows_by_job(db: AsyncSession, job_id: uuid.UUID) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting).where(MatchingShortlisting.job_id == job_id)
    )).scalars())


def add_row(db: AsyncSession, row: MatchingShortlisting) -> None:
    db.add(row)


async def flush(db: AsyncSession) -> None:
    await db.flush()


async def find_top_ranked(db: AsyncSession, job_id: uuid.UUID, top_k: int) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting)
        .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.rank.is_not(None),
               MatchingShortlisting.rank <= top_k)
        .order_by(MatchingShortlisting.rank)
    )).scalars())


async def find_candidates(db: AsyncSession, candidate_ids: list[uuid.UUID]) -> dict[uuid.UUID, Candidate]:
    return {c.id: c for c in (await db.execute(
        select(Candidate).where(Candidate.id.in_(candidate_ids))
    )).scalars()}
