"""Shortlist export as CSV or Excel, logged as Exported in the stage log."""

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser, Role
from src.constants.enums import Action, ActionCategory, CandidateStatus, PipelineStage
from src.core.services import matching_service
from src.core.services.audit_service import log_event, utcnow
from src.data.models.candidate import Candidate
from src.data.models.job import Job
from src.data.models.matching_shortlisting import MatchingShortlisting

ExportFormat = Literal["csv", "xlsx"]
ExportScope = Literal["shortlisted", "ranked", "all"]

COLUMNS = [
    ("Rank", 6), ("Name", 22), ("Email", 30), ("Phone", 16), ("Location", 14), ("Category", 14),
    ("Current title", 24), ("Experience (yrs)", 10), ("Total score", 9), ("Skills score", 9),
    ("Role score", 9), ("Experience score", 9), ("Education score", 9), ("Status", 16),
    ("Shared with hiring manager", 10), ("Recruiter notes", 30), ("Matched skills", 40),
    ("Missing skills", 30), ("Summary", 60), ("Strengths", 40), ("Gaps", 40),
]
REVIEWED = (CandidateStatus.SHORTLISTED, CandidateStatus.APPROVED,
            CandidateStatus.REJECTED, CandidateStatus.INTERVIEW_SCHEDULED)


def _num(value) -> float | None:
    return round(float(value), 2) if value is not None else None


def _skill_lists(explanation: dict | None) -> tuple[str, str]:
    skills = (explanation or {}).get("skills") or {}
    entries = (skills.get("required") or []) + (skills.get("nice") or [])
    matched, missing = [], []
    for e in entries:
        if e.get("level"):
            via = e.get("matched")
            label = e["jd_skill"] if not via or via.lower() == e["jd_skill"].lower() else f"{e['jd_skill']} (via {via})"
            matched.append(f"{label} {e['level']}")
        else:
            missing.append(e["jd_skill"])
    return "; ".join(matched), "; ".join(missing)


async def _rows(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, scope: ExportScope):
    query = (select(MatchingShortlisting, Candidate)
             .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
             .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.total_score.is_not(None)))
    if user.role == Role.HIRING_MANAGER:
        query = query.where(MatchingShortlisting.shared_to == user.id)  # only what was shared with them
    elif scope == "shortlisted":
        query = query.where(MatchingShortlisting.status.in_(REVIEWED))
    elif scope == "ranked":
        query = query.where(MatchingShortlisting.rank.is_not(None))
    rows = (await db.execute(query)).all()
    rows.sort(key=lambda rc: (rc[0].rank is None, rc[0].rank or 0, -float(rc[0].total_score)))
    return rows


def _table(rows) -> list[list]:
    table = []
    for row, cand in rows:
        e = row.explanation or {}
        matched, missing = _skill_lists(e)
        table.append([
            row.rank, cand.name, cand.email, cand.contact, cand.location, cand.category,
            cand.current_title, _num(cand.total_years_experience), _num(row.total_score),
            _num(row.llm_skill_score), _num(row.llm_role_score), _num(row.exp_score), _num(row.edu_score),
            row.status.value.replace("_", " "), "yes" if row.shared_to else "no",
            row.notes_by_recruiter or "", matched, missing, e.get("summary") or "",
            "; ".join(e.get("strengths") or []), "; ".join(e.get("gaps") or []),
        ])
    return table


def _csv(table: list[list]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([c for c, _ in COLUMNS])
    writer.writerows([["" if v is None else v for v in r] for r in table])
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8 correctly


def _xlsx(table: list[list], job: Job, weights: dict, scope: str, exported_by: str) -> bytes:
    import xlsxwriter

    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True})
    head = book.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "white",
                            "text_wrap": True, "valign": "top", "border": 1})
    wrap = book.add_format({"text_wrap": True, "valign": "top"})
    number = book.add_format({"num_format": "0.00", "valign": "top"})

    sheet = book.add_worksheet("Shortlist")
    for col, (title, width) in enumerate(COLUMNS):
        sheet.write(0, col, title, head)
        sheet.set_column(col, col, width, number if 7 <= col <= 12 else wrap)
    for r, values in enumerate(table, start=1):
        for c, value in enumerate(values):
            if value is not None:
                sheet.write(r, c, value)
    sheet.freeze_panes(1, 2)
    sheet.autofilter(0, 0, max(len(table), 1), len(COLUMNS) - 1)
    sheet.set_row(0, 32)

    about = book.add_worksheet("About")
    bold = book.add_format({"bold": True})
    info = [
        ("Job", job.title), ("Category", job.category or ""), ("Location", job.location or ""),
        ("Minimum experience", job.experience_years if job.experience_years is not None else ""),
        ("Education", job.education or ""), ("Required skills", ", ".join(job.skills or [])),
        ("Nice-to-have skills", ", ".join(job.nice_to_have_skills or [])),
        ("Weights", f"skills {weights['skill_pct']}%, role {weights['role_pct']}%, "
                    f"experience {weights['exp_pct']}%, education {weights['edu_pct']}%"),
        ("Rows", f"{len(table)} ({scope})"), ("Exported by", exported_by),
        ("Exported at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("Scores", "Each component is 0-100; total = weighted sum. Skill levels: 100 exact, "
                   "75 strongly related, 50 partly related, 0 missing."),
    ]
    about.set_column(0, 0, 22)
    about.set_column(1, 1, 90, wrap)
    for r, (k, v) in enumerate(info):
        about.write(r, 0, k, bold)
        about.write(r, 1, v)
    book.close()
    return buffer.getvalue()


async def export_shortlist(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID,
                           fmt: ExportFormat, scope: ExportScope) -> tuple[bytes, str, str]:
    """Returns (file bytes, media type, filename)."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    started = utcnow()
    request = {"format": fmt, "scope": scope if user.role != Role.HIRING_MANAGER else "shared_with_me"}
    await log_event(user_id=user.id, category=ActionCategory.EXPORTING,
                    action=Action.SHORTLIST_EXPORT_STARTED, job_id=job_id, input_data=request)
    try:
        rows = await _rows(db, user, job_id, scope)
        table = _table(rows)
        weights = (await matching_service.load_weights(db, job_id)).as_dict()
        if fmt == "csv":
            content, media = _csv(table), "text/csv; charset=utf-8"
        else:
            content = _xlsx(table, job, weights, request["scope"], f"{user.role.value} {user.id}")
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except Exception as exc:
        await log_event(user_id=user.id, category=ActionCategory.EXPORTING,
                        action=Action.SHORTLIST_EXPORT_FAILED, job_id=job_id,
                        input_data=request, output_data=str(exc)[:300], start_time=started)
        raise
    await log_event(user_id=user.id, category=ActionCategory.EXPORTING, stage=PipelineStage.EXPORTED,
                    action=Action.SHORTLIST_EXPORT_COMPLETED, job_id=job_id, input_data=request,
                    output_data={"rows": len(table), "bytes": len(content)}, start_time=started)

    slug = re.sub(r"[^A-Za-z0-9]+", "_", job.title).strip("_")[:50] or "job"
    filename = f"{slug}_shortlist_{datetime.now(timezone.utc):%Y%m%d}.{fmt}"
    return content, media, filename
