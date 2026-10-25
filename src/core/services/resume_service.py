"""Bulk resume ingestion: store -> parse (Docling) -> LLM classify + extract -> embed -> save.

Uploads return immediately with a batch id; the pipeline runs in the background and
every stage change is written to audit_logs (and the server log):

    Created -> Collecting -> Reading -> (Partly completed | Failed)

A batch that fully succeeds ends with an `extraction_completed` event whose output has
`finished: true`; partial and total failures end in Partly completed / Failed.
"""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.config.settings import settings
from src.constants.categories import normalize_category
from src.constants.enums import (
    Action,
    ActionCategory,
    CandidateStatus,
    EmbeddingKind,
    FileType,
    PipelineStage,
    Readiness,
)
from src.control.agents.prompts import resume_check as check_prompt
from src.control.agents.prompts import resume_extraction as prompt
from src.core.services.audit_service import log_event, utcnow
from src.core.services.document_parser import ParsedDocument, detect_file_type
from src.core.services.friendly_errors import friendly_error
from src.core.services.jd_service import clean_skills
from src.data.clients.postgres import AsyncSessionLocal
from src.data.clients.storage import storage
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.resume_embedding import ResumeEmbedding
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.repositories import resume_repository
from src.handlers.http_clients import embedding_client
from src.handlers.http_clients.embedding_client import EmbeddingError, EmbeddingQuotaExceeded
from src.handlers.http_clients.llm_client import LLMError, llm
from src.utils.file_types import detect_kind, format_label

logger = logging.getLogger("talentiq.resumes")

RESUME_FOLDER = "resumes"
DOC_TYPE_RESUME = "resume"
# The AI said "not a resume": kept for the recruiter to check (accept or delete).
DOC_TYPE_REVIEW = "review"

# Keep references so background tasks aren't garbage collected mid-run.
_background: set[asyncio.Task] = set()
# Live counters for running batches (the audit log is the durable record).
progress: dict[str, "BatchProgress"] = {}
_in_flight: dict[str, list["BatchItem"]] = {}  # batch id -> files being processed now


@dataclass
class UploadedFile:
    filename: str
    data: bytes
    source_site: str | None = None  # github / kaggle / huggingface / stackexchange / url


@dataclass
class BatchItem:
    document_id: uuid.UUID
    filename: str
    storage_key: str


@dataclass
class BatchProgress:
    total: int
    stage: str = PipelineStage.CREATED.value
    parsed: int = 0
    saved: int = 0
    failed: int = 0
    needs_review: int = 0  # judged "not a resume": waiting for the recruiter (not a failure)
    new_candidates: int = 0  # saved resumes of people we didn't have
    updated_candidates: int = 0  # saved resumes of people we already had (same email / phone)
    failures: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished: bool = False


@dataclass
class UploadResult:
    batch_id: str | None
    accepted: list[dict]
    rejected: list[dict]


# ---------- helpers ----------

def _batch_input(batch_id: str, **extra) -> dict:
    return {"batch_id": batch_id, **extra}


def _clean_email(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
    return match.group(0).lower() if match else None


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _years(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(min(max(float(value), 0.0), 60.0), 1)))


def _short(text: str | None, limit: int = 200) -> str | None:
    return " ".join(text.split())[:limit] if text else None


# ---------- upload ----------

async def accept_uploads(
    db: AsyncSession,
    user: CurrentUser,
    files: list[UploadedFile],
    *,
    source_type: str = "uploaded",
    source_site: str | None = None,
    batch_id: str | None = None,
    pre_rejected: list[dict] | None = None,
) -> UploadResult:
    """Validate and store files, then start the background pipeline.

    Files are judged by their content, never by their name. ``pre_rejected`` carries
    files already refused earlier (for example the wrong types found inside a zip).
    """
    rejected: list[dict] = list(pre_rejected or [])
    announce = batch_id is None  # uploads announce their own batch; sourcing runs already did
    batch_id = batch_id or str(uuid.uuid4())
    candidates: list[tuple[UploadedFile, FileType, str]] = []
    seen_hashes: set[str] = set()

    for upload in files:
        name = upload.filename or "resume"
        if not upload.data:
            rejected.append({"filename": name, "reason": "Empty file"})
        elif len(upload.data) > settings.max_upload_bytes:
            rejected.append({
                "filename": name,
                "reason": f"Larger than {settings.max_upload_bytes // (1024 * 1024)} MB",
            })
        elif (file_type := detect_file_type(upload.data)) is None:
            rejected.append({"filename": name, "reason": format_label(detect_kind(upload.data), name)})
        else:
            digest = hashlib.sha256(upload.data).hexdigest()
            if digest in seen_hashes:
                rejected.append({"filename": name, "reason": "Duplicate (same file twice)"})
            else:
                seen_hashes.add(digest)
                candidates.append((upload, file_type, digest))

    # Files already in the system: skip finished ones, re-run ones that never completed.
    existing = {}
    if candidates:
        existing = {d.file_hash: d for d in await resume_repository.find_documents_by_hashes(db, [c[2] for c in candidates])}
        extracted_ids = (await resume_repository.find_extracted_resume_ids(db, [d.id for d in existing.values()])
                         if existing else set())

    items: list[BatchItem] = []
    for upload, file_type, digest in candidates:
        name = upload.filename or "resume"
        document = existing.get(digest)
        if document is not None:
            if document.document_type == DOC_TYPE_REVIEW:
                rejected.append({"filename": name, "reason": "Already uploaded, waiting for your review"})
            elif document.document_type != DOC_TYPE_RESUME:
                rejected.append({"filename": name, "reason": "Already uploaded as a job description"})
            elif document.id in extracted_ids:
                rejected.append({"filename": name, "reason": "Already uploaded"})
            else:  # earlier attempt failed part-way: process it again
                items.append(BatchItem(document.id, name, document.file_path))
            continue

        stored_filename, key = storage.save(RESUME_FOLDER, name, upload.data)
        document = Document(
            uploaded_by=user.id,
            source_type=source_type,
            source_site=upload.source_site or source_site,
            document_type=DOC_TYPE_RESUME,
            original_filename=name,
            stored_filename=stored_filename,
            file_hash=digest,
            file_type=file_type,
            file_size=len(upload.data),
            file_path=key,
            batch_id=batch_id,
        )
        await resume_repository.add_document(db, document)
        items.append(BatchItem(document.id, name, key))
    await db.commit()

    if not items:
        return UploadResult(batch_id=None, accepted=[], rejected=rejected)

    if announce:  # sourcing runs create (and log) their batch themselves
        await log_event(
            user_id=user.id, category=ActionCategory.EXTRACTION, stage=PipelineStage.CREATED,
            input_data=_batch_input(batch_id, files=len(items), rejected=len(rejected)),
        )
    progress[batch_id] = BatchProgress(total=len(items))
    task = asyncio.create_task(_run_batch(batch_id, user.id, items))
    _background.add(task)
    task.add_done_callback(_background.discard)

    return UploadResult(
        batch_id=batch_id,
        accepted=[{"document_id": str(i.document_id), "filename": i.filename} for i in items],
        rejected=rejected,
    )


# ---------- background pipeline ----------

async def log_stage(batch_id: str, user_id: uuid.UUID, stage: PipelineStage, state: BatchProgress,
                 action: Action | None = None, **output) -> None:
    state.stage = stage.value
    await log_event(
        user_id=user_id, category=ActionCategory.EXTRACTION, stage=stage, action=action,
        input_data=_batch_input(batch_id), output_data=output or None,
    )


async def hold_item(batch_id: str, user_id: uuid.UUID, state: BatchProgress, item: BatchItem,
                    missing: list[str]) -> None:
    """A resume that lacks required fields: kept, and left for the recruiter to place in the ready or unprocessable list."""
    reason = f"Cannot process this resume: missing {', '.join(missing)}. Waiting for your review."
    state.needs_review += 1
    state.failures.append({"filename": item.filename, "reason": reason})
    await log_event(
        user_id=user_id, category=ActionCategory.EXTRACTION, action=Action.EXTRACTION_FAILED,
        input_data=_batch_input(batch_id, document_id=str(item.document_id), filename=item.filename),
        output_data=reason,
    )


async def fail_item(batch_id: str, user_id: uuid.UUID, state: BatchProgress, item: BatchItem,
                reason: str, *, needs_review: bool = False) -> None:
    if needs_review:
        state.needs_review += 1
    else:
        state.failed += 1
    state.failures.append({"filename": item.filename, "reason": reason})
    await log_event(
        user_id=user_id, category=ActionCategory.EXTRACTION, action=Action.EXTRACTION_FAILED,
        input_data=_batch_input(batch_id, document_id=str(item.document_id), filename=item.filename),
        output_data=reason,
    )
    if needs_review:
        # The AI thinks it is not a resume: keep the file for the recruiter to check.
        try:
            async with AsyncSessionLocal() as db:
                document = await resume_repository.get_document(db, item.document_id)
                if document is not None:
                    document.document_type = DOC_TYPE_REVIEW
                    await db.commit()
        except Exception:
            logger.exception("could not mark %s for review", item.filename)


async def _run_batch(batch_id: str, user_id: uuid.UUID, items: list[BatchItem]) -> None:
    """Runs the ingestion graph (src/control/agents/ingestion_graph.py) for one batch."""
    # Imported here, not at the top: ingestion_graph imports this module, so a top-level
    # import would be circular and the app would fail to start.
    from src.control.agents.ingestion_graph import ingestion_graph

    state = progress[batch_id]
    _in_flight[batch_id] = items
    try:
        await ingestion_graph.ainvoke({"batch_id": batch_id, "user_id": user_id, "items": items})
    except Exception as exc:
        logger.exception("batch %s crashed", batch_id)
        await log_stage(batch_id, user_id, PipelineStage.FAILED, state,
                        finished=True, error=friendly_error(exc, "reading the resumes"))
    finally:
        state.finished = True
        _in_flight.pop(batch_id, None)
        task = asyncio.create_task(_embed_in_background(batch_id, user_id))
        _background.add(task)
        task.add_done_callback(_background.discard)


def _running_ids() -> set[uuid.UUID]:
    return {i.document_id for items in _in_flight.values() for i in items}


async def count_failed(db: AsyncSession) -> int:
    rows = await resume_repository.find_unread_resume_documents(db, DOC_TYPE_RESUME)
    running = _running_ids()
    return sum(d.id not in running for d in rows)


async def retry_failed(db: AsyncSession, user: CurrentUser) -> UploadResult:
    """Run every failed resume through the pipeline again (one new batch)."""
    running = _running_ids()
    documents = [d for d in await resume_repository.find_unread_resume_documents(db, DOC_TYPE_RESUME) if d.id not in running]
    if not documents:
        return UploadResult(batch_id=None, accepted=[], rejected=[])
    items = [BatchItem(d.id, d.original_filename or d.stored_filename, d.file_path) for d in documents]
    batch_id = str(uuid.uuid4())
    await log_event(
        user_id=user.id, category=ActionCategory.EXTRACTION, stage=PipelineStage.CREATED,
        input_data=_batch_input(batch_id, files=len(items), rejected=0, retry_of_failed=True),
    )
    progress[batch_id] = BatchProgress(total=len(items))
    task = asyncio.create_task(_run_batch(batch_id, user.id, items))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return UploadResult(
        batch_id=batch_id,
        accepted=[{"document_id": str(i.document_id), "filename": i.filename} for i in items],
        rejected=[],
    )


def missing_required(result: prompt.ExtractedResume) -> list[str]:
    """The required fields a resume lacks (empty list when it has them all). Every resume needs: name, email,
    phone number, skills, role and education. Experience is not required: none means a fresher with 0 years."""
    missing = []
    if not (result.name or "").strip():
        missing.append("name")
    if not _clean_email(result.email):
        missing.append("email")
    if len(_digits(result.phone)) < 7:
        missing.append("phone number")
    if not clean_skills(result.skills):
        missing.append("skills")
    if not ((result.current_title or "").strip() or any((r.title or "").strip() for r in result.roles)):
        missing.append("role")
    if not result.education:
        missing.append("education")
    return missing


def has_resume_details(result: prompt.ExtractedResume) -> bool:
    """A resume names a person, or lists skills or work history."""
    return bool((result.name or "").strip() or result.skills or result.roles)


async def _check_group(texts: list[str]) -> dict[int, str]:
    """AI step 1 for a list of texts. Returns {1-based position: resume / jd / other}."""
    batch = await llm.chat_json(
        system=check_prompt.SYSTEM_PROMPT,
        user=check_prompt.build_user_prompt(texts),
        response_model=check_prompt.CheckBatch,
        max_tokens=min(4000, 40 * len(texts) + 200),
    )
    return {r.index: r.document_type for r in batch.results if 1 <= r.index <= len(texts)}


async def check_documents(texts: list[str]) -> list[str | None]:
    """Step 1 for any number of texts: "resume", "jd", "other", or None when the AI could
    not answer (those go on to extraction, so a busy AI never loses a resume)."""
    size = max(1, settings.check_batch_size)
    answers: list[str | None] = [None] * len(texts)

    async def run(start: int) -> None:
        chunk = texts[start:start + size]
        for _attempt in range(2):
            try:
                found = await _check_group(chunk)
            except LLMError:
                continue
            for position, kind in found.items():
                answers[start + position - 1] = kind
            if len(found) == len(chunk):
                return
        logger.warning("resume check incomplete for %d file(s)", sum(a is None for a in answers[start:start + size]))

    await asyncio.gather(*(run(i) for i in range(0, len(texts), size)))
    return answers


async def _extract_group(texts: list[str]) -> tuple[dict[int, prompt.ExtractedResume], str]:
    """One LLM call for a list of texts. Returns {1-based position: result}, model used."""
    batch, model = await llm.chat_json_with_model(
        system=prompt.system_prompt(),
        user=prompt.build_user_prompt(texts),
        response_model=prompt.ResumeBatch,
        max_tokens=min(16000, 1600 * len(texts) + 500),
    )
    return {r.index: r for r in batch.results if 1 <= r.index <= len(texts)}, model


async def process_group(batch_id: str, user_id: uuid.UUID, state: BatchProgress,
                         group: list[tuple[BatchItem, ParsedDocument]]) -> None:
    """AI step 2 for one group of resumes: extract the fields and save. Never raises."""
    try:
        extracted: dict[int, tuple[prompt.ExtractedResume, str]] = {}
        pending = list(range(1, len(group) + 1))

        # Ask for all of them; anything missing or invalid is asked again, in smaller pieces.
        for attempt in range(3):
            if not pending:
                break
            chunks = [pending] if attempt == 0 else [[p] for p in pending]
            still_pending: list[int] = []
            for chunk in chunks:
                try:
                    results, model = await _extract_group([group[p - 1][1].text for p in chunk])
                except LLMError:
                    still_pending.extend(chunk)
                    continue
                for local_index, position in enumerate(chunk, start=1):
                    if local_index in results:
                        extracted[position] = (results[local_index], model)
                    else:
                        still_pending.append(position)
            pending = still_pending

        for position in pending:
            await fail_item(batch_id, user_id, state, group[position - 1][0],
                        "The AI service could not read this resume. Please upload it again.")

        valid: list[tuple[BatchItem, ParsedDocument, prompt.ExtractedResume, str]] = []
        for position, (result, model) in sorted(extracted.items()):
            item, parsed = group[position - 1]
            if not has_resume_details(result):
                await fail_item(batch_id, user_id, state, item,
                                "No resume details found (name, skills or work history). "
                                "Waiting for your review.", needs_review=True)
            else:
                valid.append((item, parsed, result, model))
        if not valid:
            return

        # Save candidates now; embeddings are added afterwards for the whole batch in a few
        # paced requests (see embed_missing), so a busy embedding service never loses a resume.
        async with AsyncSessionLocal() as db:
            created = [await _save_resume(db, user_id, item, parsed, result, model)
                       for item, parsed, result, model in valid]
            await db.commit()
        held = [(item, missing_required(result)) for item, _, result, _ in valid]
        held = [(item, missing) for item, missing in held if missing]
        for item, missing in held:  # saved, but waiting for the recruiter to decide where they belong
            await hold_item(batch_id, user_id, state, item, missing)
        state.saved += len(valid) - len(held)
        state.new_candidates += sum(created)
        state.updated_candidates += len(created) - sum(created)
    except Exception as exc:
        logger.exception("group failed in batch %s", batch_id)
        for item, _ in group:
            if not any(f["filename"] == item.filename for f in state.failures):
                await fail_item(batch_id, user_id, state, item, friendly_error(exc, "reading this resume"))


async def _reopen_rejected(db: AsyncSession, candidate_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """A rejection was about the old resume. When the person sends a new one, their rejected entries go back to
    matched (not shared) with a note, so a recruiter can shortlist them again. Approved entries are left alone."""
    rows = await resume_repository.find_rejected_entries(db, candidate_id)
    note = "Resume updated after this candidate was rejected; they can be shortlisted again."
    for row in rows:
        row.status = CandidateStatus.SOURCED
        row.shared_to = None
        row.notes_by_recruiter = f"{row.notes_by_recruiter} {note}" if row.notes_by_recruiter else note
        row.updated_by = user_id


async def _save_resume(db: AsyncSession, user_id: uuid.UUID, item: BatchItem, parsed: ParsedDocument,
                       result: prompt.ExtractedResume, model: str) -> bool:
    """Save one resume. Returns True for a new person, False when it updated someone we had."""
    document = await resume_repository.get_document(db, item.document_id)
    if parsed.is_scanned and document.file_type != FileType.SCANNED_PDF:
        document.file_type = FileType.SCANNED_PDF

    skills = clean_skills(result.skills)[:40]
    email = _clean_email(result.email)
    phone = _short(result.phone, 40)
    years = _years(result.total_experience_years)
    if years is None:
        years = Decimal("0")  # no experience shown: a fresher with 0 years
    missing = missing_required(result)
    readiness = Readiness.NEEDS_REVIEW.value if missing else Readiness.READY.value
    readiness_reason = f"Missing {', '.join(missing)}" if missing else None

    candidate = None
    if email:
        candidate = await resume_repository.find_candidate_by_email(db, email)
    if candidate is None and len(_digits(phone)) >= 7:
        candidate = await resume_repository.find_candidate_by_phone_digits(db, _digits(phone))

    fields = {
        "name": _short(result.name, 120),
        "email": email,
        "contact": phone,
        "location": _short(result.location, 120),
        "current_title": _short(result.current_title, 120),
        "category": normalize_category(result.category),
        "total_years_experience": years,
        "readiness": readiness,
        "readiness_reason": readiness_reason,
    }
    created = candidate is None
    if candidate is None:
        candidate = Candidate(
            **fields, current_resume_id=document.id,
            status=CandidateStatus.SOURCED, created_by=user_id,
        )
        resume_repository.add_candidate(db, candidate)
    else:
        # A newer resume of a known person: keep their record, refresh what the new resume says.
        for key, value in fields.items():
            if value is not None:
                setattr(candidate, key, value)
        candidate.readiness_reason = readiness_reason  # cleared when nothing is missing any more
        candidate.current_resume_id = document.id
        candidate.updated_by = user_id
        candidate.deleted_at, candidate.deleted_by = None, None  # a new resume brings a deleted person back
        await _reopen_rejected(db, candidate.id, user_id)
    await resume_repository.flush(db)

    resume_repository.add_extraction(db, ResumeExtraction(
        resume_id=document.id,
        skills=skills,
        total_experience_years=years,
        education=[e.model_dump() for e in result.education],
        current_role=_short(result.current_title, 120),
        roles_and_years=[r.model_dump() for r in result.roles],
        model_version=model,
        created_by=user_id,
    ))
    await resume_repository.flush(db)
    return created


# ---------- embeddings ----------

def _roles_text(extraction: ResumeExtraction) -> str:
    titles = [extraction.current_role or ""]
    for role in (extraction.roles_and_years or []):
        if isinstance(role, dict):
            titles.append(role.get("title") or "")
    return ", ".join(clean_skills(titles)[:8])


_embed_lock = asyncio.Lock()


async def count_missing_embeddings() -> int:
    async with AsyncSessionLocal() as db:
        return await resume_repository.count_missing_embeddings(db)


async def _embed_in_background(batch_id: str, user_id: uuid.UUID) -> None:
    """Fill in missing embeddings after a batch; one run at a time."""
    if _embed_lock.locked():
        return  # another run is already working through the backlog
    async with _embed_lock:
        started = utcnow()
        try:
            left = await embed_missing(rounds=12, pause_seconds=15)
        except Exception:
            logger.exception("background embedding failed")
            return
        output = {"embeddings": "complete" if left == 0 else "incomplete", "pending": left}
        if left:
            quota = await embedding_client.quota_status()
            if quota["paused"]:
                output.update(embeddings="paused", resumes_at=quota["resumes_at"])
        await log_event(
            user_id=user_id, category=ActionCategory.EXTRACTION,
            action=Action.EXTRACTION_COMPLETED,
            input_data=_batch_input(batch_id),
            output_data=output,
            start_time=started,
        )


async def backfill_loop() -> None:
    """Every few minutes, embed resumes still missing vectors (e.g. after the daily quota resets)."""
    while True:
        await asyncio.sleep(settings.embedding_backfill_minutes * 60)
        try:
            if _embed_lock.locked() or (await embedding_client.quota_status())["paused"]:
                continue
            if await count_missing_embeddings() == 0:
                continue
            async with _embed_lock:
                left = await embed_missing(rounds=2)
            logger.info("embedding backfill done; %d still pending", left)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("embedding backfill failed")


async def embed_missing(rounds: int = 3, chunk_resumes: int = 25, pause_seconds: float = 20) -> int:
    """Embed skills and roles for every current resume that has no vectors yet.

    Safe to call at any time (it also repairs earlier failures). Texts are sent in a few
    paced requests; chunks that hit a rate limit are retried after a pause.
    Returns how many resumes still lack embeddings afterwards.
    """
    async with AsyncSessionLocal() as db:
        rows = await resume_repository.find_resumes_missing_embeddings(db)
        # Categories that have an open job are made searchable first: the free daily
        # quota goes to resumes that can be matched today.
        open_categories = await resume_repository.find_open_job_categories(db)
    rows = sorted(rows, key=lambda r: r[1] not in open_categories)  # stable: oldest first within each group

    work = []  # (candidate_id, resume_id, skills text, roles text)
    for candidate_id, _category, extraction in rows:
        skills = ", ".join(clean_skills(extraction.skills or []))
        roles = _roles_text(extraction)
        if skills or roles:
            work.append((candidate_id, extraction.resume_id, skills, roles))

    version = embedding_client.model_version()
    chunks = [work[i:i + chunk_resumes] for i in range(0, len(work), chunk_resumes)]
    for round_number in range(rounds):
        failed_chunks = []
        for chunk in chunks:
            texts = [t for _, _, skills, roles in chunk for t in (skills, roles) if t]
            try:
                vectors = iter(await embedding_client.embed_texts(texts))
            except EmbeddingQuotaExceeded as exc:
                # Daily quota used up: stop; the backfill loop resumes after the reset.
                logger.warning("embeddings paused: %s", exc)
                index = chunks.index(chunk)
                return sum(len(c) for c in failed_chunks + chunks[index:])
            except EmbeddingError as exc:
                logger.warning("embedding chunk of %d resumes failed: %s", len(chunk), exc)
                failed_chunks.append(chunk)
                continue
            async with AsyncSessionLocal() as db:
                for candidate_id, resume_id, skills, roles in chunk:
                    for kind, text in ((EmbeddingKind.SKILL, skills), (EmbeddingKind.ROLE, roles)):
                        if text:
                            resume_repository.add_embedding(db, ResumeEmbedding(
                                candidate_id=candidate_id, resume_id=resume_id, kind=kind,
                                vector=next(vectors), model_version=version,
                            ))
                await db.commit()
            await asyncio.sleep(1.0)  # pace requests
        chunks = failed_chunks
        if not chunks:
            break
        logger.info("embedding round %d: %d chunk(s) left, pausing", round_number + 1, len(chunks))
        await asyncio.sleep(pause_seconds)
    return sum(len(c) for c in chunks)
