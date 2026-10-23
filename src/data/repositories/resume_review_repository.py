"""Database access for the files waiting for the recruiter's review."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import Action
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)


async def list_documents_of_type(db: AsyncSession, document_type: str) -> list[Document]:
    return list((await db.execute(
        select(Document).where(Document.document_type == document_type).order_by(Document.created_at.desc())
    )).scalars().all())


async def latest_failure_reason(db: AsyncSession, document_id: uuid.UUID) -> str | None:
    """Why the AI said this file is not a resume (the output of its latest failed extraction)."""
    return (await db.execute(
        select(AuditLog.output_data)
        .where(AuditLog.actions == Action.EXTRACTION_FAILED,
               AuditLog.input_data.like(f'%"document_id": "{document_id}"%'))
        .order_by(AuditLog.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def get_candidate_by_resume(db: AsyncSession, document_id: uuid.UUID) -> Candidate:
    return (await db.execute(
        select(Candidate).where(Candidate.current_resume_id == document_id)
    )).scalar_one()


async def delete_document(db: AsyncSession, document: Document) -> None:
    await db.delete(document)
