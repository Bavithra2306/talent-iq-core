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

from sqlalchemy import func, select
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
)
from src.control.agents.prompts import resume_extraction as prompt
from src.core.services.audit_service import log_event, utcnow
from src.core.services.document_parser import ParsedDocument, detect_file_type, parser
from src.core.services.jd_service import clean_skills
from src.data.clients.postgres import AsyncSessionLocal
from src.data.clients.storage import storage
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.document import Document
from src.data.models.postgres.resume_embedding import ResumeEmbedding
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.handlers.http_clients import embedding_client
from src.handlers.http_clients.embedding_client import EmbeddingError, EmbeddingQuotaExceeded
from src.handlers.http_clients.llm_client import LLMError, llm

logger = logging.getLogger("talentiq.resumes")

RESUME_FOLDER = "resumes"
DOC_TYPE_RESUME = "resume"

# Keep references so background tasks aren't garbage collected mid-run.
_background: set[asyncio.Task] = set()
# Live counters for running batches (the audit log is the durable record).
progress: dict[str, "BatchProgress"] = {}


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
) -> UploadResult:
    """Validate and store files, then start the background pipeline."""
    rejected: list[dict] = []
    candidates: list[tuple[UploadedFile, FileType, str]] = []
    seen_hashes: set[str] = set()

    for upload in files:
        name = upload.filename or "resume"
        if not upload.data:
            rejected.append({"filename": name, "reason": "The file is empty."})
        elif len(upload.data) > settings.max_upload_bytes:
            rejected.append({
                "filename": name,
                "reason": f"Larger than {settings.max_upload_bytes // (1024 * 1024)} MB.",
            })
        elif (file_type := detect_file_type(name, upload.data)) is None:
            rejected.append({"filename": name, "reason": "Only PDF and DOCX files are accepted."})
        else:
            digest = hashlib.sha256(upload.data).hexdigest()
            if digest in seen_hashes:
                rejected.append({"filename": name, "reason": "Duplicate of another file in this upload."})
            else:
                seen_hashes.add(digest)
                candidates.append((upload, file_type, digest))

    # Files already in the system: skip finished ones, re-run ones that never completed.
    existing = {}
    if candidates:
        rows = await db.execute(
            select(Document).where(Document.file_hash.in_([c[2] for c in candidates]))
        )
        existing = {d.file_hash: d for d in rows.scalars()}
        extracted_ids = set(
            (await db.execute(
                select(ResumeExtraction.resume_id).where(
                    ResumeExtraction.resume_id.in_([d.id for d in existing.values()])
                )
            )).scalars()
        ) if existing else set()

    items: list[BatchItem] = []
    for upload, file_type, digest in candidates:
        name = upload.filename or "resume"
        document = existing.get(digest)
        if document is not None:
            if document.document_type != DOC_TYPE_RESUME:
                rejected.append({"filename": name, "reason": "This file was uploaded as a JD."})
            elif document.id in extracted_ids:
                rejected.append({"filename": name, "reason": "Already uploaded and processed."})
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
        )
        db.add(document)
        await db.flush()
        items.append(BatchItem(document.id, name, key))
    await db.commit()

    if not items:
        return UploadResult(batch_id=None, accepted=[], rejected=rejected)

    if batch_id is None:  # sourcing runs create (and log) their batch themselves
        batch_id = str(uuid.uuid4())
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

async def _stage(batch_id: str, user_id: uuid.UUID, stage: PipelineStage, state: BatchProgress,
                 action: Action | None = None, **output) -> None:
    state.stage = stage.value
    await log_event(
        user_id=user_id, category=ActionCategory.EXTRACTION, stage=stage, action=action,
        input_data=_batch_input(batch_id), output_data=output or None,
    )


async def _fail(batch_id: str, user_id: uuid.UUID, state: BatchProgress, item: BatchItem,
                reason: str, *, delete_file: bool = False) -> None:
    state.failed += 1
    state.failures.append({"filename": item.filename, "reason": reason})
    await log_event(
        user_id=user_id, category=ActionCategory.EXTRACTION, action=Action.EXTRACTION_FAILED,
        input_data=_batch_input(batch_id, document_id=str(item.document_id), filename=item.filename),
        output_data=reason,
    )
    if delete_file:
        # Not a resume: don't keep it around.
        try:
            async with AsyncSessionLocal() as db:
                document = await db.get(Document, item.document_id)
                if document is not None:
                    await db.delete(document)
                    await db.commit()
            storage.delete(item.storage_key)
        except Exception:
            logger.exception("could not remove rejected file %s", item.filename)


async def _run_batch(batch_id: str, user_id: uuid.UUID, items: list[BatchItem]) -> None:
    state = progress[batch_id]
    started = utcnow()
    try:
        await _stage(batch_id, user_id, PipelineStage.COLLECTING, state, files=len(items))
        await _stage(batch_id, user_id, PipelineStage.READING, state, files=len(items))

        parse_slots = asyncio.Semaphore(max(1, settings.parse_workers) * 2)
        done_queue: asyncio.Queue = asyncio.Queue()

        async def parse_one(item: BatchItem) -> None:
            async with parse_slots:
                try:
                    data = storage.read(item.storage_key)
                    result: ParsedDocument | Exception = await parser.parse(data, item.filename)
                except Exception as exc:
                    result = exc
            await done_queue.put((item, result))

        parsers = [asyncio.create_task(parse_one(i)) for i in items]
        group: list[tuple[BatchItem, ParsedDocument]] = []
        extractors: list[asyncio.Task] = []

        for _ in items:
            item, result = await done_queue.get()
            state.parsed += 1
            if isinstance(result, Exception):
                logger.warning("parse failed for %s: %s", item.filename, result)
                await _fail(batch_id, user_id, state, item, "The file could not be read.")
                continue
            if not result.text.strip():
                await _fail(batch_id, user_id, state, item, "No text could be read from the file.")
                continue
            group.append((item, result))
            if len(group) >= settings.extract_batch_size:
                extractors.append(asyncio.create_task(_process_group(batch_id, user_id, state, group)))
                group = []
        if group:
            extractors.append(asyncio.create_task(_process_group(batch_id, user_id, state, group)))

        await asyncio.gather(*parsers, *extractors)

        # Embeddings are filled in by a background task: Gemini's free quota (about 100
        # texts a minute) is slower than extraction and shouldn't hold the batch open.
        summary = {
            "finished": True, "total": state.total, "saved": state.saved,
            "failed": state.failed, "embeddings_pending": await count_missing_embeddings(),
            "seconds": round(time.monotonic() - state.started_at, 1),
        }
        if state.failed == 0:
            await _stage(batch_id, user_id, PipelineStage.READING, state,
                         action=Action.EXTRACTION_COMPLETED, **summary)
        elif state.saved > 0:
            await _stage(batch_id, user_id, PipelineStage.PARTLY_COMPLETED, state, **summary)
        else:
            await _stage(batch_id, user_id, PipelineStage.FAILED, state, **summary)
    except Exception as exc:
        logger.exception("batch %s crashed", batch_id)
        await _stage(batch_id, user_id, PipelineStage.FAILED, state,
                     finished=True, error=str(exc)[:300])
    finally:
        state.finished = True
        task = asyncio.create_task(_embed_in_background(batch_id, user_id))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def _extract_group(texts: list[str]) -> tuple[dict[int, prompt.ExtractedResume], str]:
    """One LLM call for a list of texts. Returns {1-based position: result}, model used."""
    batch, model = await llm.chat_json_with_model(
        system=prompt.system_prompt(),
        user=prompt.build_user_prompt(texts),
        response_model=prompt.ResumeBatch,
        max_tokens=min(12000, 1200 * len(texts) + 500),
    )
    return {r.index: r for r in batch.results if 1 <= r.index <= len(texts)}, model


async def _process_group(batch_id: str, user_id: uuid.UUID, state: BatchProgress,
                         group: list[tuple[BatchItem, ParsedDocument]]) -> None:
    """Extract, embed and save one group of resumes. Never raises."""
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
            await _fail(batch_id, user_id, state, group[position - 1][0],
                        "The AI service could not read this resume. Please upload it again.")

        valid: list[tuple[BatchItem, ParsedDocument, prompt.ExtractedResume, str]] = []
        for position, (result, model) in sorted(extracted.items()):
            item, parsed = group[position - 1]
            if result.document_type != "resume":
                await _fail(batch_id, user_id, state, item,
                            f"This file doesn't look like a resume (it looks like: {result.document_type}).",
                            delete_file=True)
            else:
                valid.append((item, parsed, result, model))
        if not valid:
            return

        # Save candidates now; embeddings are added afterwards for the whole batch in a few
        # paced requests (see embed_missing), so a busy embedding service never loses a resume.
        async with AsyncSessionLocal() as db:
            for item, parsed, result, model in valid:
                await _save_resume(db, user_id, item, parsed, result, model)
            await db.commit()
        state.saved += len(valid)
    except Exception as exc:
        logger.exception("group failed in batch %s", batch_id)
        for item, _ in group:
            if not any(f["filename"] == item.filename for f in state.failures):
                await _fail(batch_id, user_id, state, item, f"Unexpected error: {str(exc)[:120]}")


async def _save_resume(db: AsyncSession, user_id: uuid.UUID, item: BatchItem, parsed: ParsedDocument,
                       result: prompt.ExtractedResume, model: str) -> None:
    document = await db.get(Document, item.document_id)
    if parsed.is_scanned and document.file_type != FileType.SCANNED_PDF:
        document.file_type = FileType.SCANNED_PDF

    skills = clean_skills(result.skills)[:40]
    email = _clean_email(result.email)
    phone = _short(result.phone, 40)
    years = _years(result.total_experience_years)

    candidate = None
    if email:
        candidate = (await db.execute(
            select(Candidate).where(func.lower(Candidate.email) == email).limit(1)
        )).scalar_one_or_none()
    if candidate is None and len(_digits(phone)) >= 7:
        candidate = (await db.execute(
            select(Candidate).where(
                func.regexp_replace(Candidate.contact, r"\D", "", "g") == _digits(phone)
            ).limit(1)
        )).scalar_one_or_none()

    fields = {
        "name": _short(result.name, 120),
        "email": email,
        "contact": phone,
        "location": _short(result.location, 120),
        "current_title": _short(result.current_title, 120),
        "category": normalize_category(result.category),
        "total_years_experience": years,
    }
    if candidate is None:
        candidate = Candidate(
            **fields, current_resume_id=document.id,
            status=CandidateStatus.SOURCED, created_by=user_id,
        )
        db.add(candidate)
    else:
        # A newer resume of a known person: keep their record, refresh what the new resume says.
        for key, value in fields.items():
            if value is not None:
                setattr(candidate, key, value)
        candidate.current_resume_id = document.id
        candidate.updated_by = user_id
    await db.flush()

    db.add(ResumeExtraction(
        resume_id=document.id,
        skills=skills,
        total_experience_years=years,
        education=[e.model_dump() for e in result.education],
        current_role=_short(result.current_title, 120),
        roles_and_years=[r.model_dump() for r in result.roles],
        model_version=model,
        created_by=user_id,
    ))
    await db.flush()


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
        return (await db.execute(
            select(func.count(ResumeExtraction.id))
            .join(Candidate, Candidate.current_resume_id == ResumeExtraction.resume_id)
            .where(~select(ResumeEmbedding.id)
                   .where(ResumeEmbedding.resume_id == ResumeExtraction.resume_id)
                   .exists())
        )).scalar_one()


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
        rows = (await db.execute(
            select(Candidate.id, ResumeExtraction)
            .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
            .where(~select(ResumeEmbedding.id)
                   .where(ResumeEmbedding.resume_id == ResumeExtraction.resume_id)
                   .exists())
        )).all()

    work = []  # (candidate_id, resume_id, skills text, roles text)
    for candidate_id, extraction in rows:
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
                            db.add(ResumeEmbedding(
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
