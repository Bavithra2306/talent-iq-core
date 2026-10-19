"""Runs inside Docling worker processes (kept free of app imports so workers start fast)."""

import os
from io import BytesIO

# Below this many characters a PDF is treated as scanned and re-read with OCR.
MIN_TEXT_CHARS = 50

_converters: dict[str, object] = {}


def init_worker(threads: int) -> None:
    import torch

    torch.set_num_threads(max(1, threads))
    # Load the models now so the first real file doesn't pay for it. The OCR converter
    # is rarely needed and loads on first use.
    _converter("docx")


def noop(seconds: float) -> int:
    """Used to start every worker process at boot."""
    import time

    time.sleep(seconds)
    return os.getpid()


def _converter(kind: str):
    """kind: 'pdf' (text layer), 'pdf_ocr' (scanned) or 'docx'."""
    if kind not in _converters:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if kind == "docx":
            _converters[kind] = DocumentConverter()
        else:
            options = PdfPipelineOptions(
                artifacts_path=os.environ.get("DOCLING_ARTIFACTS_PATH"),
                do_ocr=(kind == "pdf_ocr"),
                do_table_structure=False,
            )
            _converters[kind] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
    return _converters[kind]


def _convert(data: bytes, filename: str, kind: str) -> str:
    from docling.datamodel.base_models import DocumentStream

    result = _converter(kind).convert(DocumentStream(name=filename, stream=BytesIO(data)))
    return result.document.export_to_markdown().strip()


def parse_any(data: bytes, kind: str) -> tuple[str, bool]:
    """Read a PDF, DOCX, JPG or PNG with Docling (OCR runs on scans and photos).

    ``kind`` is the detected content type; the stream is named after it because
    Docling picks its reader from the extension.
    """
    extension = {"jpeg": "jpg"}.get(kind, kind)
    text = _convert(data, f"document.{extension}", "docx")  # the all-formats converter
    return text, kind in ("jpeg", "png")


def _pdf_text_layer(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        pages = PdfReader(BytesIO(data)).pages
        return "\n".join((page.extract_text() or "") for page in pages).strip()
    except Exception:
        return ""


def _text_is_usable(text: str) -> bool:
    """False for empty (scanned) or garbled text-layer output."""
    if len(text) < MIN_TEXT_CHARS:
        return False
    if text.count("(cid:") > 5:  # font encoding failure
        return False
    return sum(c.isalpha() for c in text) / len(text) > 0.5


def parse_bytes(data: bytes, filename: str, is_docx: bool, fast_pdf: bool) -> tuple[str, bool]:
    """Returns (text, was_scanned)."""
    # Docling picks its reader from the name, so name the stream after what the file really is.
    if is_docx:
        return _convert(data, "document.docx", "docx"), False

    layer = _pdf_text_layer(data)
    if fast_pdf and _text_is_usable(layer):
        return layer, False
    if len(layer) >= MIN_TEXT_CHARS:
        # Has text but fast path is off or the text is garbled: use the full layout pipeline.
        text = _convert(data, "document.pdf", "pdf")
        if _text_is_usable(text):
            return text, False
    # No usable text layer: a scanned PDF, so read it with OCR.
    return _convert(data, "document.pdf", "pdf_ocr"), True
