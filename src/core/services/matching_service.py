"""Matching and scoring a job against the candidate pool.

    Created -> Collecting -> Reading -> Scoring -> Shortlisted   (or Partly completed / Failed)

1. Collecting: SQL filters pick the candidates; if there are more than needed, pgvector
   cosine similarity (job skill/role vectors vs resume vectors) keeps the closest ones.
2. Reading: load their extracted data; reuse stored LLM scores that are still valid.
3. Scoring: the LLM rates every JD skill and the role per candidate (0/50/75/100);
   experience and education are scored by code; the total uses the job's weights.
4. Shortlisted: rank, mark the top-k, then the stronger LLM explains each of them.

Only the four component scores (0-100) are stored, so changing weights re-ranks in code
without calling the LLM again.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.shortlist_schema import ShortlistRunRequest
from src.config.settings import settings
from src.constants.categories import normalize_category
from src.constants.enums import (
    Action,
    ActionCategory,
    CandidateStatus,
    PipelineStage,
)
from src.control.agents.prompts import match_explanation, match_scoring
from src.core.exceptions import BadRequestError, ConflictError, NotFoundError
from src.core.services import scoring_rules
from src.core.services.audit_service import log_event
from src.core.services.friendly_errors import friendly_error
from src.core.services.job_embedding_service import find_embedding, refresh_job_embedding
from src.core.services.scoring_rules import Weights
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.job import Job
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.repositories import jd_repository, matching_repository
from src.handlers.http_clients.llm_client import LLMError, llm

logger = logging.getLogger("talentiq.matching")

_running_jobs: set[uuid.UUID] = set()
_background: set[asyncio.Task] = set()


@dataclass
class RunProgress:
    job_id: uuid.UUID
    stage: str = PipelineStage.CREATED.value
    considered: int = 0
    pool: int = 0
    below_threshold: int = 0
    not_indexed: int = 0
    threshold: float | None = None  # similarity level used for this run
    scored: int = 0
    from_cache: int = 0
    shortlisted: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished: bool = False


progress: dict[str, RunProgress] = {}


@dataclass
class Pending:
    candidate: Candidate
    extraction: ResumeExtraction
    sim_skill: float | None = None
    sim_role: float | None = None
    required: list[dict] | None = None  # [{"jd_skill", "level", "matched"}]
    nice: list[dict] | None = None
    role: dict | None = None  # {"level", "matched_role"}
    industry: dict | None = None  # {"level" (None = unknown), "matched_industry"}
    from_cache: bool = False
    similarity: float | None = None  # combined cosine similarity to the job (0..1)
    summary: str | None = None  # written by the LLM in the scoring call
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


# ---------- small helpers ----------

def job_signature(job: Job) -> str:
    """Identifies the LLM-relevant parts of a job; stored scores are reused only if unchanged."""
    payload = json.dumps(
        [(job.title or "").lower(), [s.lower() for s in job.skills or []],
         [s.lower() for s in job.nice_to_have_skills or []], (job.industry or "").lower()]
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


async def load_weights(db: AsyncSession, job_id: uuid.UUID) -> Weights:
    config = await jd_repository.find_scoring_config(db, job_id)
    if config is None:
        return Weights()
    return Weights(config.skill_pct, config.role_pct, config.exp_pct, config.edu_pct)


def _avg(entries: list[dict]) -> float | None:
    return round(sum(e["level"] for e in entries) / len(entries), 2) if entries else None


def skill_score(required: list[dict]) -> float:
    """Average level of the required skills (a missing skill counts as 0). Nice-to-have
    skills are only mentioned in the explanation; they never change the score."""
    return _avg(required) or 0.0


def _years(candidate: Candidate) -> float | None:
    value = candidate.total_years_experience
    return float(value) if value is not None else None


def _role_lines(extraction: ResumeExtraction) -> list[str]:
    """'Title, Industry (2 yrs, 2019-present)' per job, most recent first."""
    lines = []
    for role in extraction.roles_and_years or []:
        if isinstance(role, dict) and role.get("title"):
            years = role.get("years")
            start, end = role.get("start_year"), "present" if role.get("current") else role.get("end_year")
            details = [f"{years:g} yrs" if isinstance(years, (int, float)) else None,
                       f"{start or '?'}-{end}" if start or end else None]
            text = role["title"] + (f", {role['industry']}" if role.get("industry") else "")
            inner = ", ".join(d for d in details if d)
            lines.append(f"{text} ({inner})" if inner else text)
    return lines[:8]


def _experience(job: Job, p: Pending) -> tuple[float, str]:
    return scoring_rules.experience_score(_years(p.candidate), job.experience_years,
                                          p.extraction.roles_and_years, list(job.skills or []))


# ---------- starting a run ----------

async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """The job a run is for (a deleted job is still returned; callers decide)."""
    return await matching_repository.get_job(db, job_id)


async def start_run(
    db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, request: ShortlistRunRequest
) -> str:
    job = await matching_repository.get_job(db, job_id)
    if job is None or job.deleted_at is not None:
        raise NotFoundError("Job not found.")
    if not job.skills:
        raise BadRequestError("The job has no required skills; add them before matching.")
    if job_id in _running_jobs:
        raise ConflictError("A matching run for this job is already in progress.")

    run_id = str(uuid.uuid4())
    _running_jobs.add(job_id)
    progress[run_id] = RunProgress(job_id=job_id)
    await log_event(
        user_id=user.id, category=ActionCategory.SHORTLISTING, stage=PipelineStage.CREATED,
        job_id=job_id,
        input_data={"run_id": run_id, "category": job.category,
                    "strong_similarity": settings.match_strong_similarity},
    )
    task = asyncio.create_task(_run(run_id, user.id, job_id, request))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return run_id


async def log_stage(run_id: str, user_id: uuid.UUID, job_id: uuid.UUID, state: RunProgress,
                 stage: PipelineStage, action: Action | None = None,
                 category: ActionCategory = ActionCategory.SHORTLISTING, **output) -> None:
    state.stage = stage.value
    await log_event(
        user_id=user_id, category=category, stage=stage, action=action, job_id=job_id,
        input_data={"run_id": run_id}, output_data=output or None,
    )


# ---------- the run ----------

async def _run(run_id: str, user_id: uuid.UUID, job_id: uuid.UUID, request: ShortlistRunRequest) -> None:
    """Runs the matching graph (src/control/agents/matching_graph.py) for one run."""
    from src.control.agents.matching_graph import matching_graph

    state = progress[run_id]
    try:
        async with AsyncSessionLocal() as db:
            await matching_graph.ainvoke(
                {"run_id": run_id, "user_id": user_id, "job_id": job_id, "request": request},
                config={"configurable": {"db": db}},
            )
    except Exception as exc:
        logger.exception("matching run %s crashed", run_id)
        await log_stage(run_id, user_id, job_id, state, PipelineStage.FAILED, action=Action.SCORING_FAILED,
                        category=ActionCategory.SCORING, finished=True,
                        error=friendly_error(exc, "scoring the candidates"))
    finally:
        state.finished = True
        _running_jobs.discard(job_id)


# ---------- step 1: candidate pool ----------

async def job_vectors(db: AsyncSession, job: Job, user_id: uuid.UUID) -> tuple[list[float] | None, list[float] | None]:
    row = await find_embedding(db, job.id)
    if row is None or (row.skill_vector is None and row.role_vector is None):
        if await refresh_job_embedding(db, job, user_id):
            row = await find_embedding(db, job.id)
    if row is None:
        return None, None
    skill = list(row.skill_vector) if row.skill_vector is not None else None
    role = list(row.role_vector) if row.role_vector is not None else None
    return skill, role


def combined_similarity(sim_skill: float | None, sim_role: float | None) -> float | None:
    """One similarity per resume: 60% skills vector, 40% role vector (whichever exist)."""
    if sim_skill is not None and sim_role is not None:
        return 0.6 * float(sim_skill) + 0.4 * float(sim_role)
    if sim_skill is not None:
        return float(sim_skill)
    if sim_role is not None:
        return float(sim_role)
    return None


async def select_candidates(
    db: AsyncSession, job: Job, job_vectors: tuple[list[float] | None, list[float] | None],
    extra_ids: list[uuid.UUID] | None = None,
) -> tuple[list[Pending], int, int, int, float]:
    """Every resume in the job's category, compared with the job.

    Takes every resume at the strong level (0.75). When fewer than match_min_strong do, the
    next most similar resumes are added, never below match_min_similarity, until
    match_fill_to are taken. Returns (candidates to score, best first; number considered;
    number left out; number without a search index yet; lowest similarity taken).
    """
    rows = await matching_repository.find_pool(db, job_vectors, normalize_category(job.category))

    indexed, not_indexed = [], 0
    for candidate, extraction, s_skill, s_role in rows:
        similarity = combined_similarity(s_skill, s_role)
        if similarity is None:
            not_indexed += 1
            continue
        p = Pending(candidate, extraction, s_skill, s_role)
        p.similarity = similarity
        indexed.append(p)

    indexed.sort(key=lambda p: (-(p.similarity or 0), -(_years(p.candidate) or 0)))
    strong = [p for p in indexed if (p.similarity or 0) >= settings.match_strong_similarity]
    if len(strong) >= settings.match_min_strong:
        chosen = strong
    else:
        usable = [p for p in indexed if (p.similarity or 0) >= settings.match_min_similarity]
        chosen = usable[:max(len(strong), settings.match_fill_to)]
    chosen = chosen[:settings.match_max_llm]
    # Candidates added by hand from another job are always scored, even outside the category or the threshold.
    if extra_ids:
        taken = {p.candidate.id for p in chosen}
        extra_rows = await matching_repository.find_pool_by_candidates(db, job_vectors, extra_ids)
        for candidate, extraction, s_skill, s_role in extra_rows:
            if candidate.id not in taken:
                p = Pending(candidate, extraction, s_skill, s_role)
                p.similarity = combined_similarity(s_skill, s_role)
                chosen.append(p)
    lowest = min((p.similarity or 0 for p in chosen), default=settings.match_strong_similarity)
    return chosen, len(rows), len(indexed) - len(chosen), not_indexed, lowest


# ---------- step 2: reuse stored scores ----------

async def apply_cache(db: AsyncSession, job: Job, pool: list[Pending], signature: str) -> None:
    rows = await matching_repository.find_rows_by_candidates(db, job.id, [p.candidate.id for p in pool])
    by_candidate = {r.candidate_id: r for r in rows}
    for p in pool:
        row = by_candidate.get(p.candidate.id)
        if row is None:
            continue
        data = row.explanation or {}
        if (row.resume_id == p.candidate.current_resume_id and data.get("job_signature") == signature
                and data.get("skills") and data.get("role") and data.get("summary")):
            p.required = data["skills"]["required"]
            p.nice = data["skills"]["nice"]
            p.role = data["role"]
            p.industry = data.get("industry")
            p.summary, p.strengths, p.gaps = data["summary"], data.get("strengths", []), data.get("gaps", [])
            p.from_cache = True


# ---------- step 3: LLM scoring ----------

def _candidate_prompt_data(job: Job, p: Pending) -> dict:
    """What the LLM sees: titles, roles and skills plus the code-computed facts it explains.
    No name, contact or other personal details."""
    _, exp_note = _experience(job, p)
    _, edu_note = scoring_rules.education_score(p.extraction.education, job.education)
    return {
        "current_title": p.candidate.current_title,
        "roles": _role_lines(p.extraction),
        "skills": (p.extraction.skills or [])[:40],
        "similarity": None if p.similarity is None else round(p.similarity * 100),
        "experience": exp_note,
        "education": edu_note,
    }


def _apply_result(job: Job, p: Pending, result: match_scoring.CandidateMatch) -> None:
    expected = list(job.skills or []) + list(job.nice_to_have_skills or [])
    if len(result.skills) == len(expected):
        by_position = list(zip(expected, result.skills, strict=False))
    else:  # the model dropped or added entries: match by name, missing = 0
        named = {s.jd_skill.strip().lower(): s for s in result.skills}
        by_position = [(name, named.get(name.lower(), match_scoring.SkillMatch())) for name in expected]
    entries = [{"jd_skill": name, "level": m.level, "matched": m.matched if m.level else None}
               for name, m in by_position]
    split = len(job.skills or [])
    p.required, p.nice = entries[:split], entries[split:]
    p.role = {"level": result.role.level,
              "matched_role": result.role.matched_role if result.role.level else None}
    p.industry = None if not job.industry or result.industry.level is None else {
        "level": result.industry.level,
        "matched_industry": result.industry.matched_industry if result.industry.level else None}
    p.summary = result.summary.strip() or None
    p.strengths = [s.strip() for s in result.strengths if s.strip()][:3]
    p.gaps = [s.strip() for s in result.gaps if s.strip()][:3]


async def _score_group(job: Job, group: list[Pending]) -> list[Pending]:
    """Score a group with one LLM call; returns the candidates it could not score."""
    user = match_scoring.build_user_prompt(
        title=job.title, category=job.category, industry=job.industry, required=list(job.skills or []),
        nice=list(job.nice_to_have_skills or []),
        candidates=[_candidate_prompt_data(job, p) for p in group],
    )
    try:
        batch = await llm.chat_json(
            system=match_scoring.SYSTEM_PROMPT, user=user, response_model=match_scoring.MatchBatch,
            max_tokens=min(16000, 1900 * len(group) + 500),
        )
    except LLMError:
        return list(group)
    by_index = {r.index: r for r in batch.results}
    missed = []
    for position, p in enumerate(group, start=1):
        if position in by_index:
            _apply_result(job, p, by_index[position])
        else:
            missed.append(p)
    return missed


async def llm_score(job: Job, todo: list[Pending]) -> list[Pending]:
    """Score all candidates; anything missed is retried one by one. Returns the failures."""
    if not todo:
        return []
    size = settings.score_batch_size
    groups = [todo[i:i + size] for i in range(0, len(todo), size)]
    missed = [p for result in await asyncio.gather(*[_score_group(job, g) for g in groups]) for p in result]
    if missed:
        singles = await asyncio.gather(*[_score_group(job, [p]) for p in missed])
        missed = [p for result in singles for p in result]
    return missed


# ---------- persistence and ranking ----------

_ROLE_WORDS = {100: "same role", 75: "closely related role", 50: "partly related role", 0: "different role"}


def _reason(p: Pending, exp_note: str, edu_note: str) -> list[str]:
    """Plain-language reasons for the score, built from the scoring data (no extra AI call)."""
    lines = []
    if p.similarity is not None:
        lines.append(f"{round(p.similarity * 100)}% matching with the job description")
    required = p.required or []
    if required:
        strong = [i["jd_skill"] for i in required if i["level"] >= 75]
        partly = [i["jd_skill"] for i in required if 0 < i["level"] < 75]
        missing = [i["jd_skill"] for i in required if i["level"] == 0]
        text = f"Required skills: {len(strong)} of {len(required)} matched"
        if partly:
            text += f"; partly: {', '.join(partly)}"
        if missing:
            text += f"; missing: {', '.join(missing)}"
        lines.append(text)
    nice = p.nice or []
    if nice:
        have = [i["jd_skill"] for i in nice if i["level"] >= 75]
        lines.append(f"Nice-to-have (not scored): {len(have)} of {len(nice)}" + (f" ({', '.join(have)})" if have else ""))
    if p.role:
        level = int(p.role.get("level") or 0)
        words = _ROLE_WORDS.get(level, f"role match {level}/100")
        matched = p.role.get("matched_role")
        lines.append(f"Role: {words}" + (f" ({matched})" if matched and level else ""))
    if p.industry:
        level = int(p.industry.get("level") or 0)
        matched = p.industry.get("matched_industry")
        words = {100: "same industry", 75: "closely related industry", 50: "some industry overlap",
                 0: "different industry"}.get(level, f"industry match {level}/100")
        lines.append(f"Industry: {words}" + (f" ({matched})" if matched and level else ""))
    if exp_note:
        lines.append(f"Experience: {exp_note}")
    if edu_note:
        lines.append(f"Education: {edu_note}")
    return lines


def _explanation_dict(job: Job, p: Pending, signature: str) -> dict:
    exp_score, exp_note = _experience(job, p)
    edu_score, edu_note = scoring_rules.education_score(p.extraction.education, job.education)
    if p.similarity is None:
        p.similarity = combined_similarity(p.sim_skill, p.sim_role)
    return {
        "job_signature": signature,
        "similarity": None if p.similarity is None else round(p.similarity, 4),
        "reason": _reason(p, exp_note, edu_note),
        "cosine": {"skills": None if p.sim_skill is None else round(float(p.sim_skill), 4),
                   "role": None if p.sim_role is None else round(float(p.sim_role), 4)},
        "skills": {"required": p.required, "nice": p.nice, "required_avg": _avg(p.required)},
        "role": p.role,
        "industry": p.industry,
        "experience": {"candidate_years": _years(p.candidate), "required_years": job.experience_years,
                       "score": exp_score, "note": exp_note},
        "education": {"required": job.education, "score": edu_score, "note": edu_note},
        # Written by the LLM with the skill and role levels; it quotes no numbers, so it
        # stays true when the recruiter changes the weights.
        "summary": p.summary,
        "strengths": p.strengths,
        "gaps": p.gaps,
    }


async def persist(db: AsyncSession, user_id: uuid.UUID, job: Job, scored: list[Pending],
                   weights: Weights, signature: str) -> None:
    """Save scores and rank this run's candidates. Nobody is shortlisted here: the recruiter
    picks the top K (with filters) afterwards. Statuses already set are left alone."""
    existing = {r.candidate_id: r for r in await matching_repository.find_rows_by_job(db, job.id)}
    entries = []
    for p in scored:
        explanation = _explanation_dict(job, p, signature)
        skills = skill_score(p.required)
        role = scoring_rules.role_industry_score(float(p.role["level"]),
                                                 (p.industry or {}).get("level"))
        exp = explanation["experience"]["score"]
        edu = explanation["education"]["score"]
        total = scoring_rules.total_score(skills, role, exp, edu, weights)

        row = existing.get(p.candidate.id)
        if row is None:
            row = MatchingShortlisting(job_id=job.id, candidate_id=p.candidate.id,
                                       status=CandidateStatus.SOURCED, created_by=user_id)
            matching_repository.add_row(db, row)
            existing[p.candidate.id] = row
        else:
            row.updated_by = user_id
        row.resume_id = p.candidate.current_resume_id
        row.llm_skill_score, row.llm_role_score = skills, role
        row.exp_score, row.edu_score, row.total_score = exp, edu, total
        row.explanation = explanation
        row.weights_used = weights.as_dict()
        entries.append((total, _years(p.candidate) or 0.0, (p.candidate.name or "").lower(), row))

    # Rank this run's candidates; rows outside this run keep their scores (as a cache) but lose their rank.
    entries.sort(key=lambda e: (-e[0], -e[1], e[2]))
    in_run = {id(e[3]) for e in entries}
    for rank, (_, _, _, row) in enumerate(entries, start=1):
        row.rank = rank
    for row in existing.values():
        if id(row) not in in_run:
            row.rank = None
    await matching_repository.flush(db)


def _set_shortlist_status(row: MatchingShortlisting, shortlisted: bool) -> None:
    """Only touch statuses the engine owns: approvals/rejections stay, and so does
    anything already shared with a hiring manager."""
    if row.shared_to is None and row.status in (CandidateStatus.SOURCED, CandidateStatus.SHORTLISTED):
        row.status = CandidateStatus.SHORTLISTED if shortlisted else CandidateStatus.SOURCED


async def unrank_all(db: AsyncSession, job_id: uuid.UUID) -> None:
    for row in await matching_repository.find_rows_by_job(db, job_id):
        row.rank = None


async def top_rows(db: AsyncSession, job_id: uuid.UUID, top_k: int) -> list[MatchingShortlisting]:
    return await matching_repository.find_top_ranked(db, job_id, top_k)


# ---------- step 4: explanations ----------

def _facts(row: MatchingShortlisting, candidate: Candidate, weights: Weights) -> dict:
    e = row.explanation or {}
    skills = e.get("skills", {})

    def fmt(items: list[dict]) -> str:
        return "; ".join(
            f"{i['jd_skill']}={i['level']}" + (f" (via {i['matched']})" if i.get("matched") else "")
            for i in items
        ) or "none"

    parts = scoring_rules.breakdown(float(row.llm_skill_score), float(row.llm_role_score),
                                    float(row.exp_score), float(row.edu_score), weights)
    role = e.get("role") or {}
    return {
        "name": candidate.name or "Candidate",
        "current_title": candidate.current_title,
        "experience": f"{_years(candidate):g} years" if _years(candidate) is not None else "not stated",
        "skills_summary": f"required [{fmt(skills.get('required', []))}]; nice-to-have [{fmt(skills.get('nice', []))}]",
        "role_summary": f"{role.get('level')}/100" + (f" (matched: {role['matched_role']})" if role.get("matched_role") else ""),
        "experience_note": (e.get("experience") or {}).get("note", ""),
        "education_note": (e.get("education") or {}).get("note", ""),
        "breakdown_text": "; ".join(
            f"{name} {p['score']:g}/100 x {p['weight']}% = {p['points']:g}" for name, p in parts.items()
        ),
        "total": float(row.total_score),
        "rank": row.rank,
    }


async def generate_explanations(
    db: AsyncSession, job: Job, rows: list[MatchingShortlisting], weights: Weights
) -> None:
    """Fallback: write a narrative for rows the scoring call left without one."""
    needed = [r for r in rows if r.explanation and not r.explanation.get("summary")]
    if not needed:
        return
    candidates = await matching_repository.find_candidates(db, [r.candidate_id for r in needed])
    job_info = {"title": job.title, "experience_years": job.experience_years, "education": job.education}
    size = settings.explain_batch_size
    groups = [needed[i:i + size] for i in range(0, len(needed), size)]

    async def explain(group: list[MatchingShortlisting]):
        facts = [_facts(r, candidates[r.candidate_id], weights) for r in group]
        try:
            batch = await llm.chat_json(
                system=match_explanation.SYSTEM_PROMPT,
                user=match_explanation.build_user_prompt(job_info, facts),
                response_model=match_explanation.ExplanationBatch,
                max_tokens=min(8000, 700 * len(group) + 300), strong=True,
            )
            return group, {x.index: x for x in batch.results}
        except LLMError:
            return group, {}

    for group, results in await asyncio.gather(*[explain(g) for g in groups]):
        for position, row in enumerate(group, start=1):
            result = results.get(position)
            if result is None or not result.summary.strip():
                continue
            updated = dict(row.explanation)
            updated.update(summary=result.summary.strip(), strengths=result.strengths[:3],
                           gaps=result.gaps[:3])
            row.explanation = updated  # new dict so the JSON change is tracked
