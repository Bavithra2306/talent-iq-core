"""Hugging Face: public resume datasets through the datasets-server API (no key needed).

The server's category filter fails on these datasets, so the first request for a
dataset pages through it once and caches a category -> row index on disk; later runs
fetch only the rows of the requested category.
"""

import asyncio
import json
import random
import re
import urllib.parse
from pathlib import Path

from src.config.settings import settings
from src.constants.categories import normalize_category
from src.handlers.http_clients.sourcing.fetcher import FetchError
from src.handlers.http_clients.sourcing.sources.base import SourcedFile, SourceResult, fetcher
from src.utils.documents import text_to_pdf

API = "https://datasets-server.huggingface.co"
DATASETS = {
    "opensporks/resumes": {"text": "Resume_str", "category": "Category", "rows": 2484},
    "ahmedheakl/resume-atlas": {"text": "Text", "category": "Category", "rows": 13389},
}
_index_locks: dict[str, asyncio.Lock] = {}


def _index_path(dataset: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", dataset.lower())
    return Path(settings.local_storage_dir) / "sources" / f"hf_{slug}_index.json"


async def _rows(dataset: str, offset: int, length: int) -> list[dict]:
    url = (f"{API}/rows?dataset={urllib.parse.quote(dataset)}&config=default&split=train"
           f"&offset={offset}&length={length}")
    result = await fetcher.get(url, check_robots=False, max_bytes=20_000_000)
    return json.loads(result.data).get("rows") or []


async def _category_index(dataset: str) -> dict[str, list[int]]:
    """{app category: [row indices]}, built once per dataset and cached on disk."""
    path = _index_path(dataset)
    lock = _index_locks.setdefault(dataset, asyncio.Lock())
    async with lock:
        if path.exists():
            return json.loads(path.read_text())
        spec = DATASETS[dataset]
        index: dict[str, list[int]] = {}
        for offset in range(0, spec["rows"], 100):
            for row in await _rows(dataset, offset, 100):
                category = normalize_category(str(row["row"].get(spec["category"]) or "")) or "Other"
                index.setdefault(category, []).append(row["row_idx"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index))
        return index


async def collect(dataset: str, category: str | None, max_results: int) -> SourceResult:
    result = SourceResult()
    spec = DATASETS.get(dataset)
    if spec is None:
        result.errors.append(f"Unknown dataset {dataset}.")
        return result
    try:
        if category:
            index = await _category_index(dataset)
            wanted = normalize_category(category)
            pool = index.get(wanted, [])
            if not pool:
                result.errors.append(f"No '{wanted}' resumes in {dataset}. "
                                     f"Available: {', '.join(sorted(index))}.")
                return result
        else:
            pool = list(range(spec["rows"]))
        picks = random.sample(pool, min(max_results, len(pool)))
        if len(pool) < max_results:
            result.note = f"Only {len(pool)} resumes of this field exist in {dataset}."
        rows = await asyncio.gather(*[_rows(dataset, i, 1) for i in picks], return_exceptions=True)
    except FetchError as exc:
        result.errors.append(f"Hugging Face is not answering right now ({exc}). Please try again later.")
        result.partial = True
        return result

    for row_idx, rows_or_error in zip(picks, rows, strict=False):
        if isinstance(rows_or_error, Exception) or not rows_or_error:
            result.partial = True
            result.errors.append(f"row {row_idx}: could not be fetched")
            continue
        row = rows_or_error[0]["row"]
        text = str(row.get(spec["text"]) or "").strip()
        if len(text) < 200:
            continue
        label = str(row.get(spec["category"]) or "").title()
        pdf = text_to_pdf(f"Resume - {label}" if label else "Resume", text[:20000],
                          footer=f"Source: Hugging Face dataset {dataset}, row {row_idx}.")
        slug = dataset.split("/")[-1]
        resume_id = str(row.get("ID") or row_idx)  # opensporks rows carry the LiveCareer resume ID
        result.files.append(SourcedFile(f"hf_{slug}_{resume_id}.pdf", pdf, "huggingface",
                                        f"https://huggingface.co/datasets/{dataset} row {row_idx}"))
    return result
