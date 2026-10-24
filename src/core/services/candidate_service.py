"""Candidate queries: batch status, listing with filters, detail, categories, resume download."""

import json
import time
import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.candidate_schema import CandidateDetail, CandidateListItem, JobStatusItem
from src.api.rest.schemas.ingestion_schema import BatchEvent, BatchStatus, RejectedFile
from src.constants.categories import normalize_category
from src.constants.enums import Action, ActionCategory, PipelineStage, Readiness
from src.core.exceptions import NotFoundError
from src.core.services import resume_service
from src.core.services.audit_service import log_event, utcnow
from src.data.clients.storage import storage
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.document import Document
from src.data.repositories import candidate_repository

SortField = Literal["date_added", "name", "experience"]


# ---------- batch status ----------

def _detail(row: AuditLog) -> str | None:
    if not row.output_data:
        return None
    try:
        data = json.loads(row.output_data)
    except ValueError:
        return row.output_data
    if isinstance(data, dict):
        return ", ".join(f"{k}={v}" for k, v in data.items() if k != "finished")
    return str(data)


async def get_batch_status(db: AsyncSession, batch_id: str) -> BatchStatus:
    rows = await candidate_repository.find_batch_events(db, batch_id)
    if not rows:
        raise NotFoundError("Batch not found.")

    events = [
        BatchEvent(at=r.created_at, stage=r.stage.value if r.stage else None,
                   action=r.actions.value if r.actions else None, detail=_detail(r))
        for r in rows
    ]
    stage_rows = [r for r in rows if r.stage is not None]
    stage = stage_rows[-1].stage.value if stage_rows else PipelineStage.CREATED.value

    pending = await resume_service.count_missing_embeddings()
    note = None
    if pending:
        quota = await resume_service.embedding_service.quota_status()
        note = (f"Daily embedding quota reached; the remaining {pending} resume(s) get vectors "
                f"after {quota['resumes_at']}. Matching still works meanwhile."
                if quota["paused"] else f"{pending} resume(s) are waiting for embeddings.")
    live = resume_service.progress.get(batch_id)
    failed_rows = [
        RejectedFile(filename=json.loads(r.input_data).get("filename", "?"), reason=r.output_data or "")
        for r in rows if r.actions and r.actions.value == "extraction_failed"
    ]
    # "Not a resume" files wait for the recruiter; they are not failures.
    review = [f for f in failed_rows if f.reason.endswith("Waiting for your review.")]
    failures = [f for f in failed_rows if not f.reason.endswith("Waiting for your review.")]
    summary: dict = {}
    for r in reversed(rows):
        if r.output_data and '"finished": true' in r.output_data:
            summary = json.loads(r.output_data)
            break

    if live is not None and not live.finished:
        return BatchStatus(
            batch_id=batch_id, stage=live.stage, finished=False, total=live.total,
            saved=live.saved, failed=live.failed, needs_review=live.needs_review,
            new_candidates=live.new_candidates, updated_candidates=live.updated_candidates,
            failures=failures, review=review, events=events,
            embeddings_pending=pending, embeddings_note=note,
            seconds=round(time.monotonic() - live.started_at, 1),
        )
    return BatchStatus(
        batch_id=batch_id, stage=stage, finished=bool(summary),
        total=summary.get("total"), saved=summary.get("saved"), failed=len(failures) if summary else None,
        needs_review=summary.get("needs_review", len(review) if summary else None),
        new_candidates=summary.get("new_candidates"), updated_candidates=summary.get("updated_candidates"),
        seconds=summary.get("seconds"), embeddings_pending=pending, embeddings_note=note,
        failures=failures, review=review, events=events,
    )


# ---------- candidates ----------

async def list_candidates(
    db: AsyncSession, *, category=None, search=None, location=None, skill=None,
    min_experience=None, max_experience=None, status_filter: str | None = None,
    source_site=None, readiness: str = "ready", origin: str | None = None, sort: SortField = "date_added", descending: bool = True,
    limit: int = 20, offset: int = 0,
) -> tuple[list[CandidateListItem], int]:
    rows, total = await candidate_repository.search(
        db, category=normalize_category(category) if category else category, search=search, location=location,
        skill=skill, min_experience=min_experience, max_experience=max_experience,
        status_filter=status_filter, source_site=source_site, readiness=readiness, origin=origin, sort_column=sort, descending=descending,
        limit=limit, offset=offset,
    )

    statuses = await job_statuses(db, [c.id for c, _, _ in rows])
    items = []
    for candidate, skills, site in rows:
        item = CandidateListItem.model_validate(candidate)
        item.skills = skills or []
        item.source_site = site
        item.job_statuses = statuses.get(candidate.id, [])
        items.append(item)
    return items, total


async def job_statuses(db: AsyncSession, candidate_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[JobStatusItem]]:
    """Each candidate's status for every job they were matched to, best score first."""
    if not candidate_ids:
        return {}
    rows = await candidate_repository.find_job_status_rows(db, candidate_ids)
    result: dict[uuid.UUID, list[JobStatusItem]] = {}
    for candidate_id, job_id, title, status_value, score in rows:
        result.setdefault(candidate_id, []).append(JobStatusItem(
            job_id=job_id, job_title=title, status=status_value,
            total_score=float(score) if score is not None else None,
        ))
    return result


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> CandidateDetail:
    row = await candidate_repository.find_detail_row(db, candidate_id)
    if row is None:
        raise NotFoundError("Candidate not found.")
    candidate, extraction, document = row
    detail = CandidateDetail.model_validate(candidate)
    detail.source_site = document.source_site
    detail.file_name = document.original_filename
    detail.job_statuses = (await job_statuses(db, [candidate.id])).get(candidate.id, [])
    if extraction is not None:
        detail.skills = extraction.skills or []
        detail.education = extraction.education
        detail.roles_and_years = extraction.roles_and_years
        detail.extraction_model = extraction.model_version
    return detail


async def list_batch_candidates(db: AsyncSession, batch_id: str) -> list[CandidateDetail]:
    """The resumes one upload or sourcing batch added, with what was read from each."""
    rows = await candidate_repository.find_batch_resumes(db, batch_id)
    statuses = await job_statuses(db, [c.id for c, _, _ in rows])
    details = []
    for candidate, extraction, document in rows:
        detail = CandidateDetail.model_validate(candidate)
        detail.source_site = document.source_site
        detail.file_name = document.original_filename
        detail.job_statuses = statuses.get(candidate.id, [])
        if extraction is not None:
            detail.skills = extraction.skills or []
            detail.education = extraction.education
            detail.roles_and_years = extraction.roles_and_years
            detail.extraction_model = extraction.model_version
        details.append(detail)
    return details


async def delete_candidate(db: AsyncSession, user: CurrentUser, candidate_id: uuid.UUID) -> None:
    """Soft delete: hide the resume everywhere (lists, matching, shortlists, review, exports).
    The row, the file and the history stay. Uploading the same person again brings them back."""
    candidate = await candidate_repository.get_candidate(db, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise NotFoundError("Candidate not found.")
    candidate_repository.mark_deleted(candidate, user.id, utcnow())
    await db.commit()
    await log_event(user_id=user.id, category=ActionCategory.EXTRACTION, action=Action.RESUME_DELETED,
                    input_data={"candidate_id": str(candidate.id), "name": candidate.name},
                    output_data="Deleted by the recruiter.")


async def readiness_counts(db: AsyncSession) -> dict[str, int]:
    counts = await candidate_repository.count_by_readiness(db)
    return {r.value: counts.get(r.value, 0) for r in Readiness}


async def set_readiness(db: AsyncSession, user: CurrentUser, candidate_id: uuid.UUID, readiness: str) -> None:
    """The recruiter decides: keep the resume in the ready list, or set it aside as unprocessable.
    The resume is only moved; nothing in it is changed or deleted."""
    candidate = await candidate_repository.get_candidate(db, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise NotFoundError("Candidate not found.")
    reason = candidate.readiness_reason if readiness != Readiness.READY.value else None
    candidate_repository.set_readiness(candidate, readiness, reason)
    candidate.updated_by = user.id
    await db.commit()
    await log_event(user_id=user.id, category=ActionCategory.EXTRACTION, action=Action.RESUME_REVIEWED,
                    input_data={"candidate_id": str(candidate.id), "name": candidate.name},
                    output_data={"readiness": readiness})


async def category_counts(db: AsyncSession) -> list[dict]:
    return [{"category": c, "count": n} for c, n in await candidate_repository.count_by_category(db)]


async def get_resume_file(db: AsyncSession, candidate_id: uuid.UUID) -> tuple[Document, bytes]:
    candidate = await candidate_repository.get_candidate(db, candidate_id)
    if candidate is None or candidate.deleted_at is not None:
        raise NotFoundError("Candidate not found.")
    document = await candidate_repository.get_document(db, candidate.current_resume_id)
    if document is None:
        raise NotFoundError("Resume file not found.")
    return document, storage.read(document.file_path)
