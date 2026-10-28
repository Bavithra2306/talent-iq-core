"""Reading and adjusting shortlists: results, weight changes, run status, NL filter, logs."""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.ingestion_schema import BatchEvent
from src.api.rest.schemas.shortlist_schema import (
    DecisionCounts,
    DecisionEvent,
    DecisionSummary,
    JobDecisions,
    LogEntry,
    ParseFilterResponse,
    RunStatus,
    ScoreParts,
    ShortlistEntry,
    ShortlistFilters,
)
from src.constants.categories import normalize_category
from src.constants.enums import Action, ActionCategory, CandidateStatus, PipelineStage
from src.control.agents.prompts import filter_parsing
from src.core.exceptions import NotFoundError, ServiceUnavailableError, UnprocessableError
from src.core.services import matching_service, scoring_rules
from src.core.services.audit_service import log_event
from src.core.services.scoring_rules import Weights
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.audit_log import AuditLog
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.scoring_config import ScoringConfig
from src.data.repositories import jd_repository, shortlist_repository
from src.handlers.http_clients.llm_client import LLMError, llm

logger = logging.getLogger("talentiq.shortlist")

SortField = Literal["score", "rank", "experience", "date_added", "name"]


# ---------- results ----------

def _entry(row: MatchingShortlisting, candidate: Candidate, skills: list[str] | None,
           weights: Weights, rank: int | None, total: float | None) -> ShortlistEntry:
    scores = ScoreParts(
        skills=_f(row.llm_skill_score), role=_f(row.llm_role_score),
        experience=_f(row.exp_score), education=_f(row.edu_score),
    )
    breakdown = None
    if None not in (scores.skills, scores.role, scores.experience, scores.education):
        breakdown = scoring_rules.breakdown(scores.skills, scores.role, scores.experience,
                                            scores.education, weights)
    explanation = row.explanation
    return ShortlistEntry(
        id=row.id, job_id=row.job_id, candidate_id=row.candidate_id, resume_id=row.resume_id,
        name=candidate.name, email=candidate.email, contact=candidate.contact,
        location=candidate.location, current_title=candidate.current_title,
        category=candidate.category, total_years_experience=_f(candidate.total_years_experience),
        skills=skills or [], status=row.status, rank=rank, total_score=total, scores=scores,
        breakdown=breakdown, explanation=explanation, weights_used=weights.as_dict(),
        notes_by_recruiter=row.notes_by_recruiter, shared_by=row.shared_by, shared_to=row.shared_to,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _f(value) -> float | None:
    return float(value) if value is not None else None


async def list_entries(
    db: AsyncSession, *, job_id: uuid.UUID | None, include_unranked: bool = False,
    status_filter: CandidateStatus | None = None, min_score: float | None = None,
    max_score: float | None = None, skill: str | None = None, location: str | None = None,
    category: str | None = None, min_experience: float | None = None,
    max_experience: float | None = None, search: str | None = None,
    education: str | None = None, skills: list[str] | None = None,
    preview_weights: Weights | None = None, sort: SortField = "score",
    descending: bool = True, limit: int = 20, offset: int = 0,
) -> tuple[list[ShortlistEntry], int, Weights]:
    stored = await matching_service.load_weights(db, job_id) if job_id else Weights()
    weights = preview_weights or stored

    rows = await shortlist_repository.find_scored_rows(
        db, job_id=job_id, include_unranked=include_unranked, status_filter=status_filter, skill=skill,
        skills=skills or [], education=education, location=location,
        category=normalize_category(category) if category else category,
        min_experience=min_experience, max_experience=max_experience, search=search,
    )

    items = []
    for row, candidate, skills in rows:
        if preview_weights is not None and None not in (
                row.llm_skill_score, row.llm_role_score, row.exp_score, row.edu_score):
            total = scoring_rules.total_score(float(row.llm_skill_score), float(row.llm_role_score),
                                              float(row.exp_score), float(row.edu_score), preview_weights)
        else:
            total = _f(row.total_score)
        items.append([row, candidate, skills, total])

    # Ranks inside one job follow the (previewed) total; across jobs they are per job.
    ranks: dict[uuid.UUID, int] = {}
    if preview_weights is not None:
        per_job: dict[uuid.UUID, list] = {}
        for it in items:
            per_job.setdefault(it[0].job_id, []).append(it)
        for group in per_job.values():
            group.sort(key=lambda it: (-(it[3] or 0), -(_f(it[1].total_years_experience) or 0)))
            for position, it in enumerate(group, start=1):
                ranks[it[0].id] = position

    if min_score is not None:
        items = [it for it in items if (it[3] or 0) >= min_score]
    if max_score is not None:
        items = [it for it in items if (it[3] or 0) <= max_score]

    keys = {
        "score": lambda it: it[3] or 0,
        "rank": lambda it: ranks.get(it[0].id, it[0].rank or 10**9),
        "experience": lambda it: _f(it[1].total_years_experience) or 0,
        "date_added": lambda it: it[0].created_at,
        "name": lambda it: (it[1].name or "").lower(),
    }
    # Equal scores keep a fixed order (name, then id) instead of whatever order the database returns.
    items.sort(key=lambda it: ((it[1].name or "").lower(), str(it[0].id)))
    items.sort(key=keys[sort], reverse=descending)
    page = items[offset:offset + limit]
    entries = [_entry(row, cand, skills, weights, ranks.get(row.id, row.rank), total)
               for row, cand, skills, total in page]
    return entries, len(items), weights


# ---------- changing weights ----------

async def apply_weights(db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, weights: Weights) -> Weights:
    """Save new weights and re-rank the job's current shortlist from the stored scores (no LLM)."""
    try:
        weights.validate()
    except ValueError as exc:
        raise UnprocessableError(str(exc)) from exc
    job = await shortlist_repository.get_job(db, job_id)
    if job is None or job.deleted_at is not None:
        raise NotFoundError("Job not found.")

    config = await jd_repository.find_scoring_config(db, job_id)
    if config is None:
        config = ScoringConfig(job_id=job_id, created_by=user.id)
        jd_repository.save_scoring_config(db, config)
    else:
        config.updated_by = user.id
    config.skill_pct, config.role_pct = weights.skill, weights.role
    config.exp_pct, config.edu_pct, config.total_pct = weights.exp, weights.edu, 100

    rows = await shortlist_repository.find_ranked_rows(db, job_id)
    ranked = []
    for row, candidate in rows:
        row.total_score = scoring_rules.total_score(
            float(row.llm_skill_score), float(row.llm_role_score),
            float(row.exp_score), float(row.edu_score), weights)
        row.weights_used = weights.as_dict()
        ranked.append((row, candidate))
    ranked.sort(key=lambda rc: (-float(rc[0].total_score), -(_f(rc[1].total_years_experience) or 0),
                                (rc[1].name or "").lower()))
    for position, (row, _) in enumerate(ranked, start=1):
        row.rank = position
    await db.commit()
    return weights


def _explain_later(job_id: uuid.UUID, row_ids: list[uuid.UUID], weights: Weights) -> None:
    task = asyncio.create_task(_write_explanations(job_id, row_ids, weights))
    matching_service._background.add(task)
    task.add_done_callback(matching_service._background.discard)


async def _write_explanations(job_id: uuid.UUID, row_ids: list[uuid.UUID], weights: Weights) -> None:
    """Fallback AI summary for shortlisted candidates the scoring call left without one (best effort)."""
    try:
        async with AsyncSessionLocal() as db:
            job = await shortlist_repository.get_job(db, job_id)
            rows = await shortlist_repository.find_rows_by_ids(db, row_ids)
            await matching_service.generate_explanations(db, job, rows, weights)
            await db.commit()
    except Exception:
        logger.exception("could not write explanations for job %s", job_id)


# ---------- picking the top K ----------

async def shortlist_top_k(
    db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, top_k: int,
    filters: ShortlistFilters, min_score: float | None = None,
) -> tuple[int, int]:
    """Shortlist the K best matched candidates that pass the filters.

    Candidates already shared with a hiring manager, approved or rejected are never
    changed. Everyone else in this job's results is set to shortlisted or back to matched.
    Returns (shortlisted, number of candidates that passed the filters).
    """
    job = await shortlist_repository.get_job(db, job_id)
    if job is None or job.deleted_at is not None:
        raise NotFoundError("Job not found.")
    entries, total, weights = await list_entries(
        db, job_id=job_id, location=filters.location, category=filters.category,
        min_experience=filters.min_experience, max_experience=filters.max_experience,
        education=filters.education, skills=filters.must_have_skills, min_score=min_score,
        sort="score", descending=True, limit=10_000,
    )
    rows = await shortlist_repository.find_rows_by_job(db, job_id)
    open_rows = [r for r in rows if r.shared_to is None
                 and r.status in (CandidateStatus.SOURCED, CandidateStatus.SHORTLISTED)]
    # The best K among those that can still change; candidates already shared or decided don't use up places.
    changeable = {r.id for r in open_rows}
    chosen = {e.id for e in [e for e in entries if e.id in changeable][:top_k]}
    picked = []
    for row in open_rows:
        row.status = CandidateStatus.SHORTLISTED if row.id in chosen else CandidateStatus.SOURCED
        row.updated_by = user.id
        if row.id in chosen:
            picked.append(row.id)
    await db.commit()
    await log_event(
        user_id=user.id, category=ActionCategory.SHORTLISTING, stage=PipelineStage.SHORTLISTED,
        action=Action.SHORTLISTING_CREATED, job_id=job_id,
        input_data={"top_k": top_k, "filters": filters.model_dump(), "min_score": min_score},
        output_data={"shortlisted": len(picked), "matching": total},
    )
    if picked:
        _explain_later(job_id, picked, weights)
    return len(picked), total


# ---------- run status and logs ----------

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


async def get_run_status(db: AsyncSession, run_id: str) -> RunStatus:
    rows = await shortlist_repository.find_run_events(db, run_id)
    if not rows:
        raise NotFoundError("Run not found.")

    events = [BatchEvent(at=r.created_at, stage=r.stage.value if r.stage else None,
                         action=r.actions.value if r.actions else None, detail=_detail(r)) for r in rows]
    stages = [r for r in rows if r.stage is not None]
    stage = stages[-1].stage.value if stages else PipelineStage.CREATED.value
    summary = {}
    for r in reversed(rows):
        if r.output_data and '"finished": true' in r.output_data:
            summary = json.loads(r.output_data)
            break

    live = matching_service.progress.get(run_id)
    if live is not None and not live.finished:
        return RunStatus(
            run_id=run_id, job_id=live.job_id, stage=live.stage, finished=False, pool=live.pool,
            considered=live.considered, below_threshold=live.below_threshold,
            not_indexed=live.not_indexed, threshold=live.threshold,
            scored=live.scored, from_cache=live.from_cache, shortlisted=live.shortlisted,
            failed=live.failed, seconds=round(time.monotonic() - live.started_at, 1), events=events)
    return RunStatus(
        run_id=run_id, job_id=rows[0].job_id, stage=stage, finished=bool(summary),
        pool=summary.get("pool"), scored=summary.get("scored"), from_cache=summary.get("from_cache"),
        considered=summary.get("considered"), below_threshold=summary.get("below_threshold"),
        not_indexed=summary.get("not_indexed"), threshold=summary.get("threshold"),
        note=summary.get("note"),
        shortlisted=summary.get("shortlisted"), failed=summary.get("failed"),
        seconds=summary.get("seconds"), events=events)


async def list_logs(
    db: AsyncSession, *, job_id: uuid.UUID | None, stage: PipelineStage | None,
    category: str | None, since: datetime | None, until: datetime | None,
    only_stages: bool, limit: int, offset: int,
) -> tuple[list[LogEntry], int]:
    rows, total = await shortlist_repository.search_logs(
        db, job_id=job_id, stage=stage, category=category, since=since, until=until,
        only_stages=only_stages, limit=limit, offset=offset,
    )
    return [
        LogEntry(id=r.id, at=r.created_at, user_id=r.user_id, job_id=r.job_id,
                 stage=r.stage.value if r.stage else None,
                 category=r.action_category.value if r.action_category else None,
                 action=r.actions.value if r.actions else None,
                 input_data=r.input_data, output_data=r.output_data, duration_ms=r.duration)
        for r in rows
    ], total


# ---------- hiring manager decisions ----------

def _add_count(counts: DecisionCounts, status: CandidateStatus, n: int) -> None:
    counts.shared += n
    if status in (CandidateStatus.APPROVED, CandidateStatus.INTERVIEW_SCHEDULED):
        counts.approved += n
    elif status == CandidateStatus.REJECTED:
        counts.rejected += n
    else:
        counts.waiting += n


def summarize_decisions(
    rows: list[tuple[uuid.UUID, str, uuid.UUID, CandidateStatus, int]],
) -> tuple[DecisionCounts, list[JobDecisions]]:
    """Totals and one line per job, from (job, title, hiring manager, status, count) rows.

    Counted from each candidate's current status, so a decision that was changed counts once.
    """
    total = DecisionCounts()
    jobs: dict[uuid.UUID, JobDecisions] = {}
    for job_id, title, manager_id, status, n in rows:
        job = jobs.setdefault(job_id, JobDecisions(job_id=job_id, job_title=title))
        if manager_id not in job.hiring_manager_ids:
            job.hiring_manager_ids.append(manager_id)
        _add_count(job, status, n)
        _add_count(total, status, n)
    return total, sorted(jobs.values(), key=lambda j: (-j.shared, j.job_title))


def _event_detail(event) -> tuple[uuid.UUID | None, str | None]:
    """The candidate id and the hiring manager's comment saved with an approve / reject event."""
    try:
        data = json.loads(event.input_data or "{}")
        return uuid.UUID(str(data.get("candidate_id"))), data.get("comment")
    except (ValueError, AttributeError):
        return None, None


async def decision_summary(db: AsyncSession, job_id: uuid.UUID | None, limit: int = 20) -> DecisionSummary:
    total, jobs = summarize_decisions(await shortlist_repository.count_shared_by_status(db, job_id))
    events = await shortlist_repository.find_decision_events(db, job_id, limit)
    details = [_event_detail(e) for e in events]
    names, titles = await shortlist_repository.find_names(
        db, [c for c, _ in details if c], list({e.job_id for e in events if e.job_id}))
    recent = [
        DecisionEvent(
            at=e.created_at, decided_by=e.user_id, job_id=e.job_id, job_title=titles.get(e.job_id),
            candidate_name=names.get(candidate_id) if candidate_id else None, comment=comment,
            decision="approved" if e.actions == Action.CANDIDATE_APPROVED else "rejected",
        )
        for e, (candidate_id, comment) in zip(events, details, strict=True)
    ]
    return DecisionSummary(**total.model_dump(), jobs=jobs, recent=recent)


# ---------- natural-language filter ----------

async def parse_filter(text: str) -> ParseFilterResponse:
    try:
        parsed = await llm.chat_json(
            system=filter_parsing.system_prompt(), user=f'Search text: "{text.strip()}"',
            response_model=filter_parsing.ParsedFilter, max_tokens=600,
        )
    except LLMError as exc:
        raise ServiceUnavailableError("The AI service is busy. Please try again in a minute.") from exc
    filters = ShortlistFilters(
        category=normalize_category(parsed.category), location=parsed.location,
        min_experience=parsed.min_experience, max_experience=parsed.max_experience,
        education=parsed.education,
        must_have_skills=[s.strip() for s in parsed.must_have_skills if s.strip()],
    )
    return ParseFilterResponse(
        original_text=text, corrected_text=parsed.corrected_text or text,
        filters=filters, top_k=parsed.top_k,
    )
