"""Kaggle: the "Resume Dataset" (snehaanbhawal) of real PDF resumes in 24 categories.

Needs KAGGLE_USERNAME and KAGGLE_KEY (free account -> Settings -> API). The zip is
downloaded once and cached; later runs only read PDFs from it.
"""

import asyncio
import base64
import io
import random
import zipfile
from pathlib import Path

from src.config.settings import settings
from src.constants.categories import normalize_category
from src.core.sourcing.fetcher import FetchError
from src.core.sourcing.sources.base import SourcedFile, SourceResult, fetcher, is_resume_file

DATASET = "snehaanbhawal/resume-dataset"
URL = f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}"
_lock = asyncio.Lock()


def configured() -> bool:
    return bool(settings.kaggle_username and settings.kaggle_key)


def _zip_path() -> Path:
    return Path(settings.local_storage_dir) / "sources" / "kaggle_resume_dataset.zip"


async def _ensure_zip() -> Path:
    path = _zip_path()
    async with _lock:
        if not path.exists():
            token = base64.b64encode(f"{settings.kaggle_username}:{settings.kaggle_key}".encode()).decode()
            result = await fetcher.get(URL, headers={"Authorization": f"Basic {token}"},
                                       check_robots=False, max_bytes=300 * 1024 * 1024)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(result.data)
    return path


def _pick(path: Path, category: str | None, max_results: int) -> list[tuple[str, bytes, str]]:
    with zipfile.ZipFile(path) as archive:
        pdfs = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
        if category:
            wanted = normalize_category(category)
            # paths look like data/data/INFORMATION-TECHNOLOGY/12345.pdf
            pdfs = [n for n in pdfs if normalize_category(Path(n).parent.name) == wanted]
        chosen = random.sample(pdfs, min(max_results, len(pdfs)))
        return [(n, archive.read(n), Path(n).parent.name) for n in chosen]


async def collect(category: str | None, max_results: int) -> SourceResult:
    result = SourceResult()
    if not configured():
        result.errors.append("Kaggle is not configured: add KAGGLE_USERNAME and KAGGLE_KEY.")
        return result
    try:
        path = await _ensure_zip()
        picked = await asyncio.to_thread(_pick, path, category, max_results)
    except (FetchError, zipfile.BadZipFile) as exc:
        _zip_path().unlink(missing_ok=True)
        result.errors.append(f"Kaggle download failed: {exc}")
        result.partial = True
        return result
    if not picked:
        result.errors.append(f"No resumes for category '{category}' in the Kaggle dataset.")
    for name, data, folder in picked:
        filename = f"kaggle_{folder.lower()}_{Path(name).stem}.pdf"
        if is_resume_file(data, filename):
            result.files.append(SourcedFile(filename, data, "kaggle", f"kaggle:{DATASET}/{name}"))
    return result
