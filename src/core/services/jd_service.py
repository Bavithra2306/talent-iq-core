"""JD flow: upload PDF -> parse (Docling) -> LLM classify + extract -> recruiter reviews -> save job.

A JD typed into the form is turned into a PDF so every job has a JD document.
"""

import hashlib
import logging
import uuid

from fpdf import FPDF
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.job_schema import JobDraft, JobExtractResponse
from src.config.settings import settings
from src.constants.categories import normalize_category
from src.constants.enums import Action, ActionCategory, FileType, JobStatus, PipelineStage
from src.control.agents.prompts import jd_extraction
from src.core.exceptions import (
    BadRequestError,
    ConflictError,
    FileTooLargeError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
    UnsupportedFileError,
)
from src.core.services.audit_service import log_event, utcnow
from src.core.services.document_parser import parser
from src.core.services.job_embedding_service import refresh_job_embedding
from src.data.clients.storage import storage
from src.data.models.postgres.document import Document
from src.data.models.postgres.job import Job
from src.data.models.postgres.scoring_config import ScoringConfig
from src.handlers.http_clients.llm_client import LLMError, llm
from src.schema.job_schema import JobCreate, JobUpdate
from src.utils.file_types import Kind, detect_kind

logger = logging.getLogger("talentiq.jd")

JD_FOLDER = "jd"
DOC_TYPE_JD = "jd"


# ---------- helpers ----------

def clean_skills(skills: list[str] | None) -> list[str]:
    """Trim, drop empties and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    result = []
    for skill in skills or []:
        name = " ".join(str(skill).split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    return result


def split_skill_lists(skills, nice) -> tuple[list[str], list[str]]:
    required = clean_skills(skills)
    required_keys = {s.lower() for s in required}
    return required, [s for s in clean_skills(nice) if s.lower() not in required_keys]


JD_KINDS = {Kind.PDF, Kind.DOCX, Kind.JPEG, Kind.PNG}


def validate_jd_file(data: bytes) -> Kind:
    """A JD is one PDF (text or scanned), DOCX, JPG or PNG, judged by its content."""
    if not data:
        raise BadRequestError("The file is empty.")
    if len(data) > settings.max_jd_bytes:
        raise FileTooLargeError(
            f"The file is larger than {settings.max_jd_bytes // (1024 * 1024)} MB.",
        )
    kind = detect_kind(data)
    if kind not in JD_KINDS:
        raise UnsupportedFileError("The JD must be a PDF, Word (DOCX), JPG or PNG file.")
    return kind


def _write(pdf: FPDF, height: float, text: str) -> None:
    # multi_cell leaves the cursor at the right edge by default; return it to the left margin.
    pdf.multi_cell(0, height, _latin1(text), new_x="LMARGIN", new_y="NEXT")


def _latin1(text: str) -> str:
    # The built-in PDF fonts only cover Latin-1.
    return text.encode("latin-1", "replace").decode("latin-1")


def render_jd_pdf(data: JobCreate) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    _write(pdf, 10, data.title)
    pdf.ln(2)

    rows = [
        ("Category", data.category),
        ("Industry", data.industry),
        ("Experience", f"{data.experience_years}+ years" if data.experience_years is not None else None),
        ("Education", data.education),
        ("Location", data.location),
        ("Work mode", data.work_mode),
        ("Required skills", ", ".join(data.skills or [])),
        ("Nice-to-have skills", ", ".join(data.nice_to_have_skills or [])),
    ]
    for label, value in rows:
        if value:
            pdf.set_font("Helvetica", "B", 11)
            _write(pdf, 7, f"{label}:")
            pdf.set_font("Helvetica", "", 11)
            _write(pdf, 7, str(value))
            pdf.ln(1)
    return bytes(pdf.output())


async def _store_document(
    db: AsyncSession,
    *,
    user: CurrentUser,
    filename: str,
    data: bytes,
    file_hash: str,
    source_type: str,
    kind: Kind = Kind.PDF,
) -> Document:
    stored_filename, key = storage.save(JD_FOLDER, filename, data)
    document = Document(
        uploaded_by=user.id,
        source_type=source_type,
        document_type=DOC_TYPE_JD,
        original_filename=filename,
        stored_filename=stored_filename,
        file_hash=file_hash,
        # Photos have no matching file_type value, so it stays empty for them.
        file_type={Kind.PDF: FileType.PDF, Kind.DOCX: FileType.DOCX}.get(kind),
        file_size=len(data),
        file_path=key,
    )
    db.add(document)
    await db.flush()
    return document


# ---------- extraction ----------

async def extract_jd(
    db: AsyncSession, user: CurrentUser, filename: str, data: bytes
) -> JobExtractResponse:
    kind = validate_jd_file(data)
    file_hash = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []

    document = (
        await db.execute(select(Document).where(Document.file_hash == file_hash))
    ).scalar_one_or_none()
    is_new_document = document is None
    if document is None:
        document = await _store_document(
            db, user=user, filename=filename, data=data,
            file_hash=file_hash, source_type="uploaded", kind=kind,
        )
        await db.commit()
    elif document.document_type != DOC_TYPE_JD:
        raise ConflictError(
            "This file was already uploaded as a resume.",
        )
    else:
        warnings.append("This JD file was uploaded before; reusing the stored copy.")

    existing_job_id = (
        await db.execute(select(Job.id).where(Job.jd_id == document.id).limit(1))
    ).scalar_one_or_none()
    if existing_job_id:
        warnings.append("A job already exists for this JD.")

    # Parse
    started = utcnow()
    try:
        parsed = await parser.parse_jd(data)
    except Exception as exc:
        logger.exception("JD parse failed")
        await log_event(
            user_id=user.id, category=ActionCategory.EXTRACTION,
            action=Action.PARSING_FAILED, input_data={"document_id": str(document.id)},
            output_data=str(exc), start_time=started,
        )
        raise UnprocessableError(
            "The file could not be read. Please upload a clearer file."
        ) from exc
    await log_event(
        user_id=user.id, category=ActionCategory.EXTRACTION,
        action=Action.PARSING_COMPLETED,
        input_data={"document_id": str(document.id), "filename": filename},
        output_data={"chars": len(parsed.text), "scanned": parsed.is_scanned},
        start_time=started,
    )
    if not parsed.text.strip():
        raise UnprocessableError(
            "We couldn't read any text from this file. Please upload a clearer file, or fill in the form by hand."
        )

    # LLM: classify + extract
    started = utcnow()
    try:
        extraction = await llm.chat_json(
            system=jd_extraction.SYSTEM_PROMPT,
            user=jd_extraction.build_user_prompt(parsed.text),
            response_model=jd_extraction.JDExtraction,
        )
    except LLMError as exc:
        await log_event(
            user_id=user.id, category=ActionCategory.EXTRACTION,
            action=Action.EXTRACTION_FAILED, input_data={"document_id": str(document.id)},
            output_data=str(exc), start_time=started,
        )
        raise ServiceUnavailableError(
            "The AI service is busy or unavailable. Please try again in a minute.",
        ) from exc

    await log_event(
        user_id=user.id, category=ActionCategory.EXTRACTION,
        action=Action.EXTRACTION_COMPLETED,
        input_data={"document_id": str(document.id)},
        output_data={"document_type": extraction.document_type},
        start_time=started,
    )

    if extraction.document_type != "jd" or extraction.job is None:
        if is_new_document:
            # Don't keep a file that turned out not to be a JD.
            storage.delete(document.file_path)
            await db.delete(document)
            await db.commit()
        raise UnprocessableError(
            "This file doesn't look like a job description"
            + (" (it looks like a resume)." if extraction.document_type == "resume" else "."),
        )

    draft = extraction.job
    draft.category = normalize_category(draft.category)
    draft.skills, draft.nice_to_have_skills = split_skill_lists(
        draft.skills, draft.nice_to_have_skills
    )
    if not draft.title:
        warnings.append("No job title was found; please enter it.")
    if not draft.skills:
        warnings.append("No required skills were found; please add them.")

    return JobExtractResponse(
        document_id=document.id,
        draft=JobDraft.model_validate(draft.model_dump()),
        existing_job_id=existing_job_id,
        warnings=warnings,
    )


# ---------- CRUD ----------

async def create_job(db: AsyncSession, user: CurrentUser, data: JobCreate) -> Job:
    data.skills, data.nice_to_have_skills = split_skill_lists(
        data.skills, data.nice_to_have_skills
    )
    data.category = normalize_category(data.category)

    if data.jd_id is not None:
        document = await db.get(Document, data.jd_id)
        if document is None or document.document_type != DOC_TYPE_JD:
            raise NotFoundError("JD document not found.")
        jd_id = document.id
    else:
        pdf_bytes = render_jd_pdf(data)
        filename = f"{data.title[:80]}.pdf"
        document = await _store_document(
            db, user=user, filename=filename, data=pdf_bytes,
            # Hash includes a uuid so two identical forms still get separate documents.
            file_hash=hashlib.sha256(pdf_bytes + uuid.uuid4().bytes).hexdigest(),
            source_type="form",
        )
        jd_id = document.id

    job = Job(
        jd_id=jd_id,
        title=data.title.strip(),
        category=data.category,
        industry=data.industry,
        experience_years=data.experience_years,
        education=data.education,
        location=data.location,
        work_mode=data.work_mode,
        status=data.status,
        skills=data.skills,
        nice_to_have_skills=data.nice_to_have_skills,
        created_by=user.id,
    )
    db.add(job)
    await db.flush()
    # Default weights: skill 40, role 20, experience 25, education 15.
    db.add(ScoringConfig(job_id=job.id, created_by=user.id))
    await db.commit()
    await db.refresh(job)
    await refresh_job_embedding(db, job, user.id)

    await log_event(
        user_id=user.id, stage=PipelineStage.CREATED, job_id=job.id,
        input_data={"source": "upload" if data.jd_id else "form"},
        output_data={"title": job.title},
    )
    return job


async def list_jobs(
    db: AsyncSession,
    *,
    status_filter: JobStatus | None,
    category: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    query = select(Job)
    if status_filter:
        query = query.where(Job.status == status_filter)
    if category:
        query = query.where(Job.category.ilike(category))
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Job.title.ilike(pattern), Job.location.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await db.execute(
        query.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return job


async def update_job(
    db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, data: JobUpdate
) -> Job:
    job = await get_job(db, job_id)
    changes = data.model_dump(exclude_unset=True)
    if "category" in changes:
        changes["category"] = normalize_category(changes["category"])
    if "skills" in changes or "nice_to_have_skills" in changes:
        changes["skills"], changes["nice_to_have_skills"] = split_skill_lists(
            changes.get("skills", job.skills), changes.get("nice_to_have_skills", job.nice_to_have_skills)
        )
    for field, value in changes.items():
        setattr(job, field, value)
    job.updated_by = user.id
    await db.commit()
    await db.refresh(job)
    if {"title", "skills", "nice_to_have_skills"} & changes.keys():
        await refresh_job_embedding(db, job, user.id)
    return job


async def get_jd_file(db: AsyncSession, job_id: uuid.UUID) -> tuple[Document, bytes]:
    job = await get_job(db, job_id)
    document = await db.get(Document, job.jd_id)
    if document is None:
        raise NotFoundError("JD document not found.")
    return document, storage.read(document.file_path)
