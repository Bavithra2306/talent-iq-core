"""Candidate queries: batch status, listing with filters, detail, categories, resume download."""

import json
import time
import uuid
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import CandidateStatus, PipelineStage
from src.constants.categories import normalize_category
from src.core.services.query_helpers import skill_clause
from src.core.services import resume_service
from src.core.services.storage_service import storage
from src.data.models.audit_log import AuditLog
from src.data.models.candidate import Candidate
from src.data.models.document import Document
from src.data.models.resume_extraction import ResumeExtraction
from src.data.models.job import Job
from src.data.models.matching_shortlisting import MatchingShortlisting
from src.schema.candidate_schema import CandidateDetail, CandidateListItem, JobStatusItem
from src.schema.ingestion_schema import BatchEvent, BatchStatus, RejectedFile

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
    rows = (await db.execute(
        select(AuditLog)
        .where(AuditLog.input_data.like(f'%"batch_id": "{batch_id}"%'))
        .order_by(AuditLog.created_at, AuditLog.id)
    )).scalars().all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found.")

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
    failures = [
        RejectedFile(filename=json.loads(r.input_data).get("filename", "?"), reason=r.output_data or "")
        for r in rows if r.actions and r.actions.value == "extraction_failed"
    ]
    summary: dict = {}
    for r in reversed(rows):
        if r.output_data and '"finished": true' in r.output_data:
            summary = json.loads(r.output_data)
            break

    if live is not None and not live.finished:
        return BatchStatus(
            batch_id=batch_id, stage=live.stage, finished=False, total=live.total,
            saved=live.saved, failed=live.failed, failures=failures, events=events,
            embeddings_pending=pending, embeddings_note=note,
            seconds=round(time.monotonic() - live.started_at, 1),
        )
    return BatchStatus(
        batch_id=batch_id, stage=stage, finished=bool(summary),
        total=summary.get("total"), saved=summary.get("saved"), failed=summary.get("failed"),
        seconds=summary.get("seconds"), embeddings_pending=pending, embeddings_note=note,
        failures=failures, events=events,
    )


# ---------- candidates ----------

def _apply_filters(query, *, category, search, location, skill, min_experience,
                   max_experience, status_filter, source_site):
    if category:
        query = query.where(Candidate.category == normalize_category(category))
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Candidate.name.ilike(pattern), Candidate.email.ilike(pattern),
                                Candidate.current_title.ilike(pattern)))
    if location:
        query = query.where(Candidate.location.ilike(f"%{location}%"))
    if skill:
        query = query.where(skill_clause(ResumeExtraction.skills, skill))
    if min_experience is not None:
        query = query.where(Candidate.total_years_experience >= min_experience)
    if max_experience is not None:
        query = query.where(Candidate.total_years_experience <= max_experience)
    if status_filter == "new":
        # Not matched to any job yet.
        query = query.where(~select(MatchingShortlisting.id)
                            .where(MatchingShortlisting.candidate_id == Candidate.id).exists())
    elif status_filter:
        # Has this status for at least one job.
        query = query.where(select(MatchingShortlisting.id).where(
            MatchingShortlisting.candidate_id == Candidate.id,
            MatchingShortlisting.status == CandidateStatus(status_filter),
        ).exists())
    if source_site:
        query = query.where(Document.source_site == source_site)
    return query


async def list_candidates(
    db: AsyncSession, *, category=None, search=None, location=None, skill=None,
    min_experience=None, max_experience=None, status_filter: str | None = None,
    source_site=None, sort: SortField = "date_added", descending: bool = True,
    limit: int = 20, offset: int = 0,
) -> tuple[list[CandidateListItem], int]:
    base = (
        select(Candidate, ResumeExtraction.skills, Document.source_site)
        .join(Document, Document.id == Candidate.current_resume_id)
        .outerjoin(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
    )
    base = _apply_filters(
        base, category=category, search=search, location=location, skill=skill,
        min_experience=min_experience, max_experience=max_experience,
        status_filter=status_filter, source_site=source_site,
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    column = {
        "date_added": Candidate.created_at,
        "name": func.lower(Candidate.name),
        "experience": Candidate.total_years_experience,
    }[sort]
    ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
    rows = (await db.execute(base.order_by(ordering, Candidate.id).limit(limit).offset(offset))).all()

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
    rows = await db.execute(
        select(MatchingShortlisting.candidate_id, MatchingShortlisting.job_id, Job.title,
               MatchingShortlisting.status, MatchingShortlisting.total_score)
        .join(Job, Job.id == MatchingShortlisting.job_id)
        .where(MatchingShortlisting.candidate_id.in_(candidate_ids))
        .order_by(MatchingShortlisting.total_score.desc().nulls_last())
    )
    result: dict[uuid.UUID, list[JobStatusItem]] = {}
    for candidate_id, job_id, title, status_value, score in rows.all():
        result.setdefault(candidate_id, []).append(JobStatusItem(
            job_id=job_id, job_title=title, status=status_value,
            total_score=float(score) if score is not None else None,
        ))
    return result


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> CandidateDetail:
    row = (await db.execute(
        select(Candidate, ResumeExtraction, Document)
        .join(Document, Document.id == Candidate.current_resume_id)
        .outerjoin(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .where(Candidate.id == candidate_id)
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")
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


async def category_counts(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(Candidate.category, func.count()).group_by(Candidate.category).order_by(func.count().desc())
    )
    return [{"category": c, "count": n} for c, n in rows.all()]


async def get_resume_file(db: AsyncSession, candidate_id: uuid.UUID) -> tuple[Document, bytes]:
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found.")
    document = await db.get(Document, candidate.current_resume_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume file not found.")
    return document, storage.read(document.file_path)
