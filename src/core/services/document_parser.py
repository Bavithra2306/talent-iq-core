"""Docling-based document parsing (PDF now; DOCX and scanned PDFs are added with resume ingestion)."""

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger("talentiq.parser")

# Below this many characters a PDF is treated as scanned and re-read with OCR.
MIN_TEXT_CHARS = 50


@dataclass
class ParsedDocument:
    text: str
    is_scanned: bool


class DocumentParser:
    def __init__(self):
        self._converters: dict[bool, DocumentConverter] = {}
        # Docling converters are not safe to share across threads.
        self._lock = threading.Lock()

    def _converter(self, ocr: bool) -> DocumentConverter:
        if ocr not in self._converters:
            options = PdfPipelineOptions(
                artifacts_path=os.environ.get("DOCLING_ARTIFACTS_PATH"),
                do_ocr=ocr,
                do_table_structure=False,
            )
            self._converters[ocr] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converters[ocr]

    def _convert(self, data: bytes, filename: str, ocr: bool) -> str:
        with self._lock:
            result = self._converter(ocr).convert(
                DocumentStream(name=filename, stream=BytesIO(data))
            )
        return result.document.export_to_markdown().strip()

    def parse_pdf_sync(self, data: bytes, filename: str) -> ParsedDocument:
        text = self._convert(data, filename, ocr=False)
        if len(text) >= MIN_TEXT_CHARS:
            return ParsedDocument(text=text, is_scanned=False)
        logger.info("little text in %s; retrying with OCR", filename)
        return ParsedDocument(text=self._convert(data, filename, ocr=True), is_scanned=True)

    async def parse_pdf(self, data: bytes, filename: str) -> ParsedDocument:
        return await asyncio.to_thread(self.parse_pdf_sync, data, filename)


parser = DocumentParser()
