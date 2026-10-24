"""Database access for resumes: uploaded files, candidates, extracted fields and resume embeddings."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import CandidateStatus, JobStatus
from src.data.models.postgres.candidate import Candidate, usable_candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_embedding import ResumeEmbedding
from src.data.models.postgres.resume_extraction import ResumeExtraction

# ---------- documents ----------

async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)


async def find_documents_by_hashes(db: AsyncSession, file_hashes: list[str]) -> list[Document]:
    return list((await db.execute(select(Document).where(Document.file_hash.in_(file_hashes)))).scalars())


async def find_extracted_resume_ids(db: AsyncSession, document_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these resume files were already read into extracted fields."""
    return set((await db.execute(
        select(ResumeExtraction.resume_id).where(ResumeExtraction.resume_id.in_(document_ids))
    )).scalars())


async def add_document(db: AsyncSession, document: Document) -> None:
    db.add(document)
    await db.flush()


async def find_unread_resume_documents(db: AsyncSession, document_type: str) -> list[Document]:
    """Resume files stored but never read into a candidate (the AI or a reader failed)."""
    return list((await db.execute(
        select(Document).where(
            Document.document_type == document_type,
            ~select(ResumeExtraction.id).where(ResumeExtraction.resume_id == Document.id).exists(),
        )
    )).scalars())


# ---------- candidates ----------

async def find_candidate_by_email(db: AsyncSession, email: str) -> Candidate | None:
    """A known candidate with this e-mail address (email is already lower case)."""
    return (await db.execute(
        select(Candidate).where(func.lower(Candidate.email) == email).limit(1)
    )).scalar_one_or_none()


async def find_candidate_by_phone_digits(db: AsyncSession, digits: str) -> Candidate | None:
    """A known candidate whose phone number has exactly these digits."""
    return (await db.execute(
        select(Candidate).where(
            func.regexp_replace(Candidate.contact, r"\D", "", "g") == digits
        ).limit(1)
    )).scalar_one_or_none()


def add_candidate(db: AsyncSession, candidate: Candidate) -> None:
    db.add(candidate)


def add_extraction(db: AsyncSession, extraction: ResumeExtraction) -> None:
    db.add(extraction)


async def flush(db: AsyncSession) -> None:
    await db.flush()


async def find_rejected_entries(db: AsyncSession, candidate_id: uuid.UUID) -> list[MatchingShortlisting]:
    """The candidate's result rows (one per job) that a hiring manager rejected."""
    return list((await db.execute(select(MatchingShortlisting).where(
        MatchingShortlisting.candidate_id == candidate_id,
        MatchingShortlisting.status == CandidateStatus.REJECTED,
    ))).scalars().all())


# ---------- embeddings ----------

async def count_missing_embeddings(db: AsyncSession) -> int:
    """Current resumes that have no embedding yet."""
    return (await db.execute(
        select(func.count(ResumeExtraction.id))
        .join(Candidate, Candidate.current_resume_id == ResumeExtraction.resume_id)
        .where(usable_candidate())
        .where(~select(ResumeEmbedding.id)
               .where(ResumeEmbedding.resume_id == ResumeExtraction.resume_id)
               .exists())
    )).scalar_one()


async def find_resumes_missing_embeddings(db: AsyncSession) -> list[tuple[uuid.UUID, str | None, ResumeExtraction]]:
    """(candidate id, category, extraction) of every current resume without embeddings, oldest first."""
    rows = (await db.execute(
        select(Candidate.id, Candidate.category, ResumeExtraction)
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .where(usable_candidate())
        .where(~select(ResumeEmbedding.id)
               .where(ResumeEmbedding.resume_id == ResumeExtraction.resume_id)
               .exists())
        .order_by(Candidate.created_at)
    )).all()
    return [tuple(r) for r in rows]


async def find_open_job_categories(db: AsyncSession) -> set[str | None]:
    """Categories that have an open job."""
    return set((await db.execute(
        select(Job.category).where(Job.status == JobStatus.OPEN, Job.deleted_at.is_(None))
    )).scalars())


def add_embedding(db: AsyncSession, embedding: ResumeEmbedding) -> None:
    db.add(embedding)


async def count_embeddings_since(db: AsyncSession, start: datetime) -> int:
    """Resume embeddings created since a moment (used for the daily quota)."""
    return (await db.execute(
        select(func.count()).where(ResumeEmbedding.created_at >= start)
    )).scalar_one()
