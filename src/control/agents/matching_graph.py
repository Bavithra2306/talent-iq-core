"""Matching and shortlisting graph.

    collect -> (empty pool? done) -> read_cache -> score -> explain -> finish

* collect: loads the job and weights, finds the candidate pool (filters + similarity).
* read_cache: reuses scores from earlier runs of the same job (same job signature).
* score: the LLM scores the rest, then totals and ranks are saved.
* explain: short explanations for the shortlisted candidates (best effort).
* finish: writes the final stage (shortlisted / partly completed / failed).

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
    shortlisted: int


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
    top_k = state["request"].top_k

    await _stage(state, PipelineStage.COLLECTING)
    vectors = await svc.job_vectors(db, job, state["user_id"])
    pool_size = min(settings.pool_max, max(settings.pool_min, top_k * settings.pool_multiplier))
    pool, matching_total = await svc.select_pool(db, state["request"].filters, pool_size, vectors)
    progress.pool = len(pool)
    if not pool:
        await svc.unrank_all(db, state["job_id"])
        await db.commit()
        await _stage(state, PipelineStage.SHORTLISTED, finished=True, pool=0, matching=0, scored=0,
                     from_cache=0, shortlisted=0, failed=0,
                     note="No candidates match the filters.", seconds=_seconds(state))
    return {"job": job, "weights": weights, "signature": svc.job_signature(job),
            "pool": pool, "matching_total": matching_total}


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

    await svc.persist(db, state["user_id"], state["job"], scored, state["weights"], state["signature"],
                      state["request"].top_k, pool_ids={p.candidate.id for p in pool})
    await db.commit()
    await _stage(state, PipelineStage.SCORING, action=Action.SCORING_FINISHED,
                 category=ActionCategory.SCORING, scored=len(scored), failed=len(failed))
    return {"scored": scored, "failed": failed}


async def explain(state: MatchingState, config: RunnableConfig) -> dict:
    db, progress = _db(config), svc.progress[state["run_id"]]
    rows = await svc.top_rows(db, state["job_id"], state["request"].top_k)
    progress.shortlisted = len(rows)
    await svc.generate_explanations(db, state["job"], rows, state["weights"])
    await db.commit()
    return {"shortlisted": len(rows)}


async def finish(state: MatchingState) -> dict:
    progress = svc.progress[state["run_id"]]
    failed, scored = state["failed"], state["scored"]
    summary = {
        "finished": True, "pool": len(state["pool"]), "matching": state["matching_total"],
        "scored": len(scored), "from_cache": progress.from_cache,
        "shortlisted": state["shortlisted"], "failed": len(failed), "seconds": _seconds(state),
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
                     ("explain", explain), ("finish", finish)):
        graph.add_node(name, fn)
    graph.add_edge(START, "collect")
    graph.add_conditional_edges("collect", after_collect, {"read_cache": "read_cache", END: END})
    graph.add_edge("read_cache", "score")
    graph.add_edge("score", "explain")
    graph.add_edge("explain", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


matching_graph = build_matching_graph()
