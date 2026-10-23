"""Files the AI judged "not a resume", kept for the recruiter to check.

The recruiter opens the file and either confirms it is a resume (it is then read
and saved like any other resume) or deletes it. These files are documents with
``document_type = "review"``; no extra table is needed.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.ingestion_schema import ReviewAccepted, ReviewFile
from src.constants.enums import Action, ActionCategory
from src.core.exceptions import NotFoundError, ServiceUnavailableError, UnprocessableError
from src.core.services import resume_service
from src.core.services.audit_service import log_event
from src.core.services.document_parser import parser
from src.data.clients.storage import storage
from src.data.models.postgres.document import Document
from src.data.repositories import resume_review_repository
from src.handlers.http_clients.llm_client import LLMError

logger = logging.getLogger("talentiq.resumes")



async def _get(db: AsyncSession, document_id: uuid.UUID) -> Document:
    document = await resume_review_repository.get_document(db, document_id)
    if document is None or document.document_type != resume_service.DOC_TYPE_REVIEW:
        raise NotFoundError("This file is not waiting for review.")
    return document


async def _reason(db: AsyncSession, document_id: uuid.UUID) -> str | None:
    return await resume_review_repository.latest_failure_reason(db, document_id)


async def list_files(db: AsyncSession) -> list[ReviewFile]:
    documents = await resume_review_repository.list_documents_of_type(db, resume_service.DOC_TYPE_REVIEW)
    return [
        ReviewFile(
            document_id=d.id, filename=d.original_filename or "file",
            file_type=d.file_type.value if d.file_type else None, file_size=d.file_size,
            source_site=d.source_site, uploaded_at=d.created_at, reason=await _reason(db, d.id),
        )
        for d in documents
    ]


async def get_file(db: AsyncSession, document_id: uuid.UUID) -> tuple[Document, bytes]:
    document = await _get(db, document_id)
    return document, storage.read(document.file_path)


async def accept_as_resume(db: AsyncSession, user: CurrentUser, document_id: uuid.UUID) -> ReviewAccepted:
    """The recruiter says it is a resume: read it, extract the fields and save the candidate."""
    document = await _get(db, document_id)
    data = storage.read(document.file_path)
    parsed = await parser.parse(data, document.original_filename or "resume")
    if not parsed.text.strip():
        raise UnprocessableError("No text could be read from this file.")
    try:
        # The recruiter confirmed it: skip step 1 and go straight to extraction.
        results, model = await resume_service._extract_group([parsed.text])
    except LLMError as exc:
        raise ServiceUnavailableError("The AI service is busy. Please try again in a minute.") from exc
    result = results.get(1)
    if result is None:
        raise ServiceUnavailableError("The AI service could not read this file. Please try again.")
    if not resume_service.has_resume_details(result):
        # Guard against a wrong click: nothing that looks like a resume was found.
        raise UnprocessableError(
            "We couldn't find resume details (name, skills or work history) in this file. "
            "Check it again or delete it."
        )

    item = resume_service.BatchItem(document.id, document.original_filename or "resume", document.file_path)
    document.document_type = resume_service.DOC_TYPE_RESUME
    await resume_service._save_resume(db, user.id, item, parsed, result, model)
    await db.commit()
    candidate = await resume_review_repository.get_candidate_by_resume(db, document.id)
    await log_event(
        user_id=user.id, category=ActionCategory.EXTRACTION, action=Action.EXTRACTION_COMPLETED,
        input_data={"document_id": str(document.id), "filename": document.original_filename,
                    "reviewed_by_recruiter": True},
        output_data={"candidate_id": str(candidate.id), "name": candidate.name},
    )
    # Its search index is filled in by the background embedding loop.
    return ReviewAccepted(candidate_id=candidate.id, name=candidate.name,
                          message=f"Saved as a resume{f' for {candidate.name}' if candidate.name else ''}.")


async def delete_file(db: AsyncSession, user: CurrentUser, document_id: uuid.UUID) -> None:
    """The recruiter confirms it is not a resume: remove the record and the stored file."""
    document = await _get(db, document_id)
    key, name = document.file_path, document.original_filename
    await resume_review_repository.delete_document(db, document)
    await db.commit()
    try:
        storage.delete(key)
    except Exception:
        logger.exception("could not delete stored file %s", key)
    await log_event(
        user_id=user.id, category=ActionCategory.EXTRACTION, action=Action.EXTRACTION_FAILED,
        input_data={"document_id": str(document_id), "filename": name},
        output_data="Deleted by the recruiter after review: not a resume.",
    )
