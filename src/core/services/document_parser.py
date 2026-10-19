"""Docling-based document parsing for PDF, DOCX and scanned PDF.

Docling is CPU heavy, so files are parsed in a pool of worker processes (several
resumes at once) without blocking the API's event loop.
"""

import asyncio
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from src.config.settings import settings
from src.constants.enums import FileType
from src.core.services import parse_worker
from src.utils.file_types import Kind, detect_kind

logger = logging.getLogger("talentiq.parser")


@dataclass
class ParsedDocument:
    text: str
    is_scanned: bool


def detect_file_type(data: bytes) -> FileType | None:
    """PDF or DOCX judged by the file's content only (scanned PDFs are found while parsing)."""
    kind = detect_kind(data)
    if kind is Kind.PDF:
        return FileType.PDF
    if kind is Kind.DOCX:
        return FileType.DOCX
    return None


class DocumentParser:
    def __init__(self):
        self._pool: ProcessPoolExecutor | None = None

    def _get_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            workers = max(1, settings.parse_workers)
            threads = max(1, (os.cpu_count() or 4) // workers)
            self._pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=parse_worker.init_worker,
                initargs=(threads,),
            )
        return self._pool

    async def parse(self, data: bytes, filename: str) -> ParsedDocument:
        is_docx = detect_kind(data) is Kind.DOCX
        loop = asyncio.get_running_loop()
        for attempt in (1, 2):
            try:
                text, scanned = await loop.run_in_executor(
                    self._get_pool(), parse_worker.parse_bytes, data, filename, is_docx, settings.pdf_fast_path
                )
                return ParsedDocument(text=text, is_scanned=scanned)
            except BrokenProcessPool:
                # A worker died (e.g. out of memory on a huge file): rebuild the pool once.
                logger.error("parser pool broke while reading %s", filename)
                self._pool = None
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    async def warm_up(self) -> None:
        """Start all worker processes (and load their models) in the background."""
        loop = asyncio.get_running_loop()
        pool = self._get_pool()
        started = await asyncio.gather(
            *[loop.run_in_executor(pool, parse_worker.noop, 1.0) for _ in range(settings.parse_workers)]
        )
        logger.info("parser ready: %d worker processes", len(set(started)))

    async def parse_jd(self, data: bytes) -> ParsedDocument:
        """Job descriptions (PDF, scanned PDF, DOCX, JPG, PNG) are all read by Docling alone."""
        loop = asyncio.get_running_loop()
        text, scanned = await loop.run_in_executor(
            self._get_pool(), parse_worker.parse_any, data, detect_kind(data).value
        )
        return ParsedDocument(text=text, is_scanned=scanned)


parser = DocumentParser()
