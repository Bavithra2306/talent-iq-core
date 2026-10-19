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

from sqlalchemy import Float, String, and_, cast, null, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.shortlist_schema import ShortlistFilters, ShortlistRunRequest
from src.config.settings import settings
from src.constants.categories import normalize_category
from src.constants.enums import (
    Action,
    ActionCategory,
    CandidateStatus,
    EmbeddingKind,
    PipelineStage,
)
from src.control.agents.prompts import match_explanation, match_scoring
from src.core.exceptions import BadRequestError, ConflictError, NotFoundError
from src.core.services import scoring_rules
from src.core.services.audit_service import log_event
from src.core.services.job_embedding_service import refresh_job_embedding
from src.core.services.query_helpers import skill_clause
from src.core.services.scoring_rules import Weights
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.job import Job
from src.data.models.postgres.job_embedding import JobEmbedding
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting
from src.data.models.postgres.resume_embedding import ResumeEmbedding
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.models.postgres.scoring_config import ScoringConfig
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
    from_cache: bool = False
    old_explanation: dict | None = None
    similarity: float | None = None  # combined cosine similarity to the job (0..1)


# ---------- small helpers ----------

def job_signature(job: Job) -> str:
    """Identifies the LLM-relevant parts of a job; stored scores are reused only if unchanged."""
    payload = json.dumps(
        [(job.title or "").lower(), [s.lower() for s in job.skills or []],
         [s.lower() for s in job.nice_to_have_skills or []]]
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


async def load_weights(db: AsyncSession, job_id: uuid.UUID) -> Weights:
    config = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.job_id == job_id)
    )).scalar_one_or_none()
    if config is None:
        return Weights()
    return Weights(config.skill_pct, config.role_pct, config.exp_pct, config.edu_pct)


def _avg(entries: list[dict]) -> float | None:
    return round(sum(e["level"] for e in entries) / len(entries), 2) if entries else None


def skill_score(required: list[dict], nice: list[dict]) -> float:
    """Required skills average; nice-to-have skills can add up to 10% of the score."""
    required_avg = _avg(required) or 0.0
    nice_avg = _avg(nice)
    return round(required_avg if nice_avg is None else 0.9 * required_avg + 0.1 * nice_avg, 2)


def _years(candidate: Candidate) -> float | None:
    value = candidate.total_years_experience
    return float(value) if value is not None else None


def _role_lines(extraction: ResumeExtraction) -> list[str]:
    lines = []
    for role in extraction.roles_and_years or []:
        if isinstance(role, dict) and role.get("title"):
            years = role.get("years")
            lines.append(f"{role['title']} ({years:g} yrs)" if isinstance(years, (int, float)) else role["title"])
    return lines[:8]


def _filter_clauses(filters: ShortlistFilters) -> list:
    clauses = []
    if filters.category:
        clauses.append(Candidate.category == normalize_category(filters.category))
    if filters.location:
        clauses.append(Candidate.location.ilike(f"%{filters.location.strip()}%"))
    if filters.min_experience is not None:
        clauses.append(Candidate.total_years_experience >= filters.min_experience)
    if filters.max_experience is not None:
        clauses.append(Candidate.total_years_experience <= filters.max_experience)
    if filters.education:
        clauses.append(cast(ResumeExtraction.education, String).ilike(f"%{filters.education.strip()}%"))
    for skill in filters.must_have_skills:
        if skill.strip():
            clauses.append(skill_clause(ResumeExtraction.skills, skill))
    return clauses


# ---------- starting a run ----------

async def start_run(
    db: AsyncSession, user: CurrentUser, job_id: uuid.UUID, request: ShortlistRunRequest
) -> str:
    job = await db.get(Job, job_id)
    if job is None:
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
                    "similarity_levels": settings.match_similarity_levels},
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
                        category=ActionCategory.SCORING, finished=True, error=str(exc)[:300])
    finally:
        state.finished = True
        _running_jobs.discard(job_id)


# ---------- step 1: candidate pool ----------

async def job_vectors(db: AsyncSession, job: Job, user_id: uuid.UUID) -> tuple[list[float] | None, list[float] | None]:
    row = (await db.execute(select(JobEmbedding).where(JobEmbedding.job_id == job.id).limit(1))).scalar_one_or_none()
    if row is None or (row.skill_vector is None and row.role_vector is None):
        if await refresh_job_embedding(db, job, user_id):
            row = (await db.execute(select(JobEmbedding).where(JobEmbedding.job_id == job.id).limit(1))).scalar_one_or_none()
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
) -> tuple[list[Pending], int, int, int, float]:
    """Every resume in the job's category, compared with the job.

    Uses the first similarity level (0.75) that at least one resume reaches, falling back
    to the next ones (0.60, then 0.50). Returns (candidates at or above that level, best
    first; number considered; number below it; number without a search index yet; level).
    """
    skill_vec, role_vec = job_vectors
    skill_emb, role_emb = aliased(ResumeEmbedding), aliased(ResumeEmbedding)
    sim_skill = (1 - skill_emb.vector.cosine_distance(skill_vec)) if skill_vec else cast(null(), Float)
    sim_role = (1 - role_emb.vector.cosine_distance(role_vec)) if role_vec else cast(null(), Float)
    query = (
        select(Candidate, ResumeExtraction, sim_skill.label("sim_skill"), sim_role.label("sim_role"))
        .join(ResumeExtraction, ResumeExtraction.resume_id == Candidate.current_resume_id)
        .outerjoin(skill_emb, and_(skill_emb.resume_id == Candidate.current_resume_id,
                                   skill_emb.kind == EmbeddingKind.SKILL))
        .outerjoin(role_emb, and_(role_emb.resume_id == Candidate.current_resume_id,
                                  role_emb.kind == EmbeddingKind.ROLE))
    )
    category = normalize_category(job.category)
    if category:
        query = query.where(Candidate.category == category)
    rows = (await db.execute(query)).all()

    indexed, not_indexed = [], 0
    for candidate, extraction, s_skill, s_role in rows:
        similarity = combined_similarity(s_skill, s_role)
        if similarity is None:
            not_indexed += 1
            continue
        p = Pending(candidate, extraction, s_skill, s_role)
        p.similarity = similarity
        indexed.append(p)

    levels = sorted(settings.match_similarity_levels, reverse=True) or [0.75]
    level, similar = levels[-1], []
    for level in levels:
        similar = [p for p in indexed if (p.similarity or 0) >= level]
        if similar:
            break
    similar.sort(key=lambda p: (-(p.similarity or 0), -(_years(p.candidate) or 0)))
    return similar[:settings.match_max_llm], len(rows), len(indexed) - len(similar), not_indexed, level


# ---------- step 2: reuse stored scores ----------

async def apply_cache(db: AsyncSession, job: Job, pool: list[Pending], signature: str) -> None:
    rows = (await db.execute(
        select(MatchingShortlisting).where(
            MatchingShortlisting.job_id == job.id,
            MatchingShortlisting.candidate_id.in_([p.candidate.id for p in pool]),
        )
    )).scalars().all()
    by_candidate = {r.candidate_id: r for r in rows}
    for p in pool:
        row = by_candidate.get(p.candidate.id)
        if row is None:
            continue
        p.old_explanation = row.explanation
        data = row.explanation or {}
        if (row.resume_id == p.candidate.current_resume_id and data.get("job_signature") == signature
                and data.get("skills") and data.get("role")):
            p.required = data["skills"]["required"]
            p.nice = data["skills"]["nice"]
            p.role = data["role"]
            p.from_cache = True


# ---------- step 3: LLM scoring ----------

def _candidate_prompt_data(p: Pending) -> dict:
    return {
        "current_title": p.candidate.current_title,
        "roles": _role_lines(p.extraction),
        "skills": (p.extraction.skills or [])[:40],
    }


def _apply_result(job: Job, p: Pending, result: match_scoring.CandidateMatch) -> None:
    expected = list(job.skills or []) + list(job.nice_to_have_skills or [])
    if len(result.skills) == len(expected):
        by_position = list(zip(expected, result.skills))
    else:  # the model dropped or added entries: match by name, missing = 0
        named = {s.jd_skill.strip().lower(): s for s in result.skills}
        by_position = [(name, named.get(name.lower(), match_scoring.SkillMatch())) for name in expected]
    entries = [{"jd_skill": name, "level": m.level, "matched": m.matched if m.level else None}
               for name, m in by_position]
    split = len(job.skills or [])
    p.required, p.nice = entries[:split], entries[split:]
    p.role = {"level": result.role.level,
              "matched_role": result.role.matched_role if result.role.level else None}


async def _score_group(job: Job, group: list[Pending]) -> list[Pending]:
    """Score a group with one LLM call; returns the candidates it could not score."""
    user = match_scoring.build_user_prompt(
        title=job.title, category=job.category, required=list(job.skills or []),
        nice=list(job.nice_to_have_skills or []),
        candidates=[_candidate_prompt_data(p) for p in group],
    )
    try:
        batch = await llm.chat_json(
            system=match_scoring.SYSTEM_PROMPT, user=user, response_model=match_scoring.MatchBatch,
            max_tokens=min(12000, 1500 * len(group) + 500),
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
        lines.append(f"{round(p.similarity * 100)}% similar to the job description")
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
        lines.append(f"Nice-to-have: {len(have)} of {len(nice)}" + (f" ({', '.join(have)})" if have else ""))
    if p.role:
        level = int(p.role.get("level") or 0)
        words = _ROLE_WORDS.get(level, f"role match {level}/100")
        matched = p.role.get("matched_role")
        lines.append(f"Role: {words}" + (f" ({matched})" if matched and level else ""))
    if exp_note:
        lines.append(f"Experience: {exp_note}")
    if edu_note:
        lines.append(f"Education: {edu_note}")
    return lines


def _explanation_dict(job: Job, p: Pending, signature: str) -> dict:
    exp_score, exp_note = scoring_rules.experience_score(_years(p.candidate), job.experience_years)
    edu_score, edu_note = scoring_rules.education_score(p.extraction.education, job.education)
    old = p.old_explanation or {}
    same_job = old.get("job_signature") == signature
    if p.similarity is None:
        p.similarity = combined_similarity(p.sim_skill, p.sim_role)
    return {
        "job_signature": signature,
        "similarity": None if p.similarity is None else round(p.similarity, 4),
        "reason": _reason(p, exp_note, edu_note),
        "cosine": {"skills": None if p.sim_skill is None else round(float(p.sim_skill), 4),
                   "role": None if p.sim_role is None else round(float(p.sim_role), 4)},
        "skills": {"required": p.required, "nice": p.nice,
                   "required_avg": _avg(p.required), "nice_avg": _avg(p.nice)},
        "role": p.role,
        "experience": {"candidate_years": _years(p.candidate), "required_years": job.experience_years,
                       "score": exp_score, "note": exp_note},
        "education": {"required": job.education, "score": edu_score, "note": edu_note},
        # Keep an earlier narrative only while it still describes the same job.
        "summary": old.get("summary") if same_job else None,
        "strengths": old.get("strengths", []) if same_job else [],
        "gaps": old.get("gaps", []) if same_job else [],
        "explained_total": old.get("explained_total") if same_job else None,
    }


async def persist(db: AsyncSession, user_id: uuid.UUID, job: Job, scored: list[Pending],
                   weights: Weights, signature: str) -> None:
    """Save scores and rank this run's candidates. Nobody is shortlisted here: the recruiter
    picks the top K (with filters) afterwards. Statuses already set are left alone."""
    existing = {
        r.candidate_id: r for r in (await db.execute(
            select(MatchingShortlisting).where(MatchingShortlisting.job_id == job.id)
        )).scalars()
    }
    entries = []
    for p in scored:
        explanation = _explanation_dict(job, p, signature)
        skills = skill_score(p.required, p.nice)
        role = float(p.role["level"])
        exp = explanation["experience"]["score"]
        edu = explanation["education"]["score"]
        total = scoring_rules.total_score(skills, role, exp, edu, weights)

        row = existing.get(p.candidate.id)
        if row is None:
            row = MatchingShortlisting(job_id=job.id, candidate_id=p.candidate.id,
                                       status=CandidateStatus.SOURCED, created_by=user_id)
            db.add(row)
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
    await db.flush()


def _set_shortlist_status(row: MatchingShortlisting, shortlisted: bool) -> None:
    """Only touch statuses the engine owns: approvals/rejections stay, and so does
    anything already shared with a hiring manager."""
    if row.shared_to is None and row.status in (CandidateStatus.SOURCED, CandidateStatus.SHORTLISTED):
        row.status = CandidateStatus.SHORTLISTED if shortlisted else CandidateStatus.SOURCED


async def unrank_all(db: AsyncSession, job_id: uuid.UUID) -> None:
    rows = (await db.execute(select(MatchingShortlisting).where(MatchingShortlisting.job_id == job_id))).scalars()
    for row in rows:
        row.rank = None


async def top_rows(db: AsyncSession, job_id: uuid.UUID, top_k: int) -> list[MatchingShortlisting]:
    return list((await db.execute(
        select(MatchingShortlisting)
        .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.rank.is_not(None),
               MatchingShortlisting.rank <= top_k)
        .order_by(MatchingShortlisting.rank)
    )).scalars())


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
    """Write a narrative for rows whose explanation is missing or no longer matches their total."""
    needed = [r for r in rows if r.explanation and (
        not r.explanation.get("summary") or r.explanation.get("explained_total") != float(r.total_score))]
    if not needed:
        return
    candidates = {c.id: c for c in (await db.execute(
        select(Candidate).where(Candidate.id.in_([r.candidate_id for r in needed]))
    )).scalars()}
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
                           gaps=result.gaps[:3], explained_total=float(row.total_score))
            row.explanation = updated  # new dict so the JSON change is tracked
