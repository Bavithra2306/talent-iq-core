"""Matching graph.

    collect -> (nobody similar enough? done) -> read_cache -> score -> finish

* collect: every resume in the job's category; experience and education are scored by
  code, and each resume's cosine similarity to the job is computed. Only resumes with
  similarity from the threshold (0.75) up to 1.0 go on.
* read_cache: reuses LLM scores from earlier runs of the same job (same job signature).
* score: the LLM scores skills and role for the rest; totals use the job's weights and
  every candidate gets plain-language reasons. Nobody is shortlisted automatically.
* finish: writes the final stage (shortlisted / partly completed / failed).

The recruiter then filters the results and picks the top K to shortlist.

The database session comes from the run config (``config["configurable"]["db"]``),
not from the state, so the state stays small.
"""

import logging
import time
import uuid
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.api.rest.schemas.shortlist_schema import ShortlistRunRequest
from src.config.settings import settings
from src.constants.enums import Action, ActionCategory, PipelineStage
from src.core.services import matching_service as svc
from src.core.services.scoring_rules import Weights
from src.data.models.postgres.job import Job

logger = logging.getLogger("talentiq.matching")


class MatchingState(TypedDict, total=False):
    run_id: str
    user_id: uuid.UUID
    job_id: uuid.UUID
    request: ShortlistRunRequest
    job: Job
    weights: Weights
    signature: str
    pool: list[svc.Pending]
    matching_total: int
    scored: list[svc.Pending]
    failed: list[svc.Pending]
    counts: dict


def _db(config: RunnableConfig) -> Any:
    return config["configurable"]["db"]


async def _stage(state: MatchingState, stage: PipelineStage, **kw: Any) -> None:
    await svc.log_stage(state["run_id"], state["user_id"], state["job_id"],
                        svc.progress[state["run_id"]], stage, **kw)


def _seconds(state: MatchingState) -> float:
    return round(time.monotonic() - svc.progress[state["run_id"]].started_at, 1)


async def collect(state: MatchingState, config: RunnableConfig) -> dict:
    db, progress = _db(config), svc.progress[state["run_id"]]
    job = await db.get(Job, state["job_id"])
    weights = await svc.load_weights(db, state["job_id"])
    await _stage(state, PipelineStage.COLLECTING, job_category=job.category)
    vectors = await svc.job_vectors(db, job, state["user_id"])
    pool, considered, below, not_indexed = await svc.select_candidates(db, job, vectors)
    progress.considered, progress.pool = considered, len(pool)
    progress.below_threshold, progress.not_indexed = below, not_indexed
    counts = {"considered": considered, "below_threshold": below, "not_indexed": not_indexed,
              "threshold": settings.match_similarity_threshold}
    if not pool:
        await svc.unrank_all(db, state["job_id"])
        await db.commit()
        if considered == 0:
            note = f"There are no resumes in the {job.category or 'job'} category yet."
        elif not_indexed == considered:
            note = "The resumes are still being added to search. Please try again later."
        else:
            note = f"No resume reached {round(settings.match_similarity_threshold * 100)}% similarity."
        await _stage(state, PipelineStage.SHORTLISTED, finished=True, pool=0, scored=0,
                     from_cache=0, shortlisted=0, failed=0, note=note, seconds=_seconds(state), **counts)
    return {"job": job, "weights": weights, "signature": svc.job_signature(job),
            "pool": pool, "matching_total": considered, "counts": counts}


def after_collect(state: MatchingState) -> str:
    return "read_cache" if state["pool"] else END


async def read_cache(state: MatchingState, config: RunnableConfig) -> dict:
    progress, pool = svc.progress[state["run_id"]], state["pool"]
    await _stage(state, PipelineStage.READING, pool=len(pool), matching=state["matching_total"])
    await svc.apply_cache(_db(config), state["job"], pool, state["signature"])
    progress.from_cache = sum(p.from_cache for p in pool)
    return {}


async def score(state: MatchingState, config: RunnableConfig) -> dict:
    db, progress, pool = _db(config), svc.progress[state["run_id"]], state["pool"]
    await _stage(state, PipelineStage.SCORING, action=Action.SCORING_STARTED,
                 category=ActionCategory.SCORING, to_score=len(pool) - progress.from_cache,
                 from_cache=progress.from_cache)
    failed = await svc.llm_score(state["job"], [p for p in pool if p.required is None])
    scored = [p for p in pool if p.required is not None]
    progress.failed, progress.scored = len(failed), len(scored)

    await svc.persist(db, state["user_id"], state["job"], scored, state["weights"], state["signature"])
    await db.commit()
    await _stage(state, PipelineStage.SCORING, action=Action.SCORING_FINISHED,
                 category=ActionCategory.SCORING, scored=len(scored), failed=len(failed))
    return {"scored": scored, "failed": failed}


async def finish(state: MatchingState) -> dict:
    progress = svc.progress[state["run_id"]]
    failed, scored = state["failed"], state["scored"]
    summary = {
        "finished": True, "pool": len(state["pool"]), **state["counts"],
        "scored": len(scored), "from_cache": progress.from_cache,
        "shortlisted": 0, "failed": len(failed), "seconds": _seconds(state),
    }
    if failed and not scored:
        await _stage(state, PipelineStage.FAILED, action=Action.SCORING_FAILED,
                     category=ActionCategory.SCORING, **summary)
    elif failed:
        await _stage(state, PipelineStage.PARTLY_COMPLETED, **summary)
    else:
        await _stage(state, PipelineStage.SHORTLISTED, action=Action.SHORTLISTING_CREATED, **summary)
    return {}


def build_matching_graph():
    graph = StateGraph(MatchingState)
    for name, fn in (("collect", collect), ("read_cache", read_cache), ("score", score),
                     ("finish", finish)):
        graph.add_node(name, fn)
    graph.add_edge(START, "collect")
    graph.add_conditional_edges("collect", after_collect, {"read_cache": "read_cache", END: END})
    graph.add_edge("read_cache", "score")
    graph.add_edge("score", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


matching_graph = build_matching_graph()
