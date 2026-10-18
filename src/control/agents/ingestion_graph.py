"""Resume ingestion graph.

    collect -> read_files -> (no readable files? finish) -> extract_save -> finish

* collect: marks the batch as started (stage ``collecting``).
* read_files: parses every file in parallel (pypdf / Docling); unreadable files are
  marked failed and the rest continue.
* extract_save: LLM extraction in groups, then saves candidates; not-a-resume files
  and LLM failures are marked failed per file.
* finish: writes the final stage (finished / partly completed / failed).

Embeddings are filled in afterwards by a background task (see resume_service).
"""

import asyncio
import logging
import time
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.config.settings import settings
from src.constants.enums import Action, PipelineStage
from src.core.services import resume_service as svc
from src.core.services.document_parser import ParsedDocument, parser
from src.data.clients.storage import storage

logger = logging.getLogger("talentiq.resumes")


class IngestionState(TypedDict, total=False):
    batch_id: str
    user_id: uuid.UUID
    items: list[svc.BatchItem]
    readable: list[tuple[svc.BatchItem, ParsedDocument]]


async def collect(state: IngestionState) -> dict:
    progress = svc.progress[state["batch_id"]]
    await svc.log_stage(state["batch_id"], state["user_id"], PipelineStage.COLLECTING, progress,
                        files=len(state["items"]))
    return {}


async def read_files(state: IngestionState) -> dict:
    batch_id, user_id, items = state["batch_id"], state["user_id"], state["items"]
    progress = svc.progress[batch_id]
    await svc.log_stage(batch_id, user_id, PipelineStage.READING, progress, files=len(items))

    slots = asyncio.Semaphore(max(1, settings.parse_workers) * 2)

    async def parse_one(item: svc.BatchItem) -> tuple[svc.BatchItem, ParsedDocument | Exception]:
        async with slots:
            try:
                return item, await parser.parse(storage.read(item.storage_key), item.filename)
            except Exception as exc:
                return item, exc

    readable: list[tuple[svc.BatchItem, ParsedDocument]] = []
    for item, result in await asyncio.gather(*(parse_one(i) for i in items)):
        progress.parsed += 1
        if isinstance(result, Exception):
            logger.warning("parse failed for %s: %s", item.filename, result)
            await svc.fail_item(batch_id, user_id, progress, item, "The file could not be read.")
        elif not result.text.strip():
            await svc.fail_item(batch_id, user_id, progress, item, "No text could be read from the file.")
        else:
            readable.append((item, result))
    return {"readable": readable}


def after_read(state: IngestionState) -> str:
    return "extract_save" if state.get("readable") else "finish"


async def extract_save(state: IngestionState) -> dict:
    batch_id, user_id = state["batch_id"], state["user_id"]
    progress = svc.progress[batch_id]
    readable = state["readable"]
    size = settings.extract_batch_size
    groups = [readable[i:i + size] for i in range(0, len(readable), size)]
    await asyncio.gather(*(svc.process_group(batch_id, user_id, progress, g) for g in groups))
    return {}


async def finish(state: IngestionState) -> dict:
    batch_id, user_id = state["batch_id"], state["user_id"]
    progress = svc.progress[batch_id]
    summary = {
        "finished": True, "total": progress.total, "saved": progress.saved,
        "failed": progress.failed, "embeddings_pending": await svc.count_missing_embeddings(),
        "seconds": round(time.monotonic() - progress.started_at, 1),
    }
    if progress.failed == 0:
        await svc.log_stage(batch_id, user_id, PipelineStage.READING, progress,
                            action=Action.EXTRACTION_COMPLETED, **summary)
    elif progress.saved > 0:
        await svc.log_stage(batch_id, user_id, PipelineStage.PARTLY_COMPLETED, progress, **summary)
    else:
        await svc.log_stage(batch_id, user_id, PipelineStage.FAILED, progress, **summary)
    return {}


def build_ingestion_graph():
    graph = StateGraph(IngestionState)
    graph.add_node("collect", collect)
    graph.add_node("read_files", read_files)
    graph.add_node("extract_save", extract_save)
    graph.add_node("finish", finish)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "read_files")
    graph.add_conditional_edges("read_files", after_read,
                                {"extract_save": "extract_save", "finish": "finish"})
    graph.add_edge("extract_save", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


ingestion_graph = build_ingestion_graph()
