"""Detect a file's real type from its content (magic bytes), never from its name.

A DOCX is a zip archive of XML files, so ``PK`` alone cannot tell DOCX from a plain
zip, Excel or PowerPoint file. For zip containers the names inside are checked too.
"""

import zipfile
from enum import Enum
from io import BytesIO
from typing import IO


class Kind(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    JPEG = "jpeg"
    PNG = "png"
    ZIP = "zip"  # a plain archive
    XLSX = "xlsx"
    PPTX = "pptx"
    DAMAGED = "damaged"  # starts like a zip but cannot be opened
    UNKNOWN = "unknown"


LABELS = {
    Kind.PDF: "a PDF",
    Kind.DOCX: "a Word document",
    Kind.JPEG: "a JPG image",
    Kind.PNG: "a PNG image",
    Kind.ZIP: "a zip archive",
    Kind.XLSX: "an Excel file",
    Kind.PPTX: "a PowerPoint file",
    Kind.DAMAGED: "a damaged zip or Office file",
    Kind.UNKNOWN: "an unsupported file type",
}

MEDIA_TYPES = {
    Kind.PDF: "application/pdf",
    Kind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    Kind.JPEG: "image/jpeg",
    Kind.PNG: "image/png",
}

# Extension Docling needs in the stream name to pick the right reader.
EXTENSIONS = {Kind.PDF: "pdf", Kind.DOCX: "docx", Kind.JPEG: "jpg", Kind.PNG: "png"}

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


def _zip_kind(file: IO[bytes]) -> Kind:
    try:
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return Kind.DAMAGED
    if "[Content_Types].xml" in names:
        if "word/document.xml" in names:
            return Kind.DOCX
        if "xl/workbook.xml" in names:
            return Kind.XLSX
        if "ppt/presentation.xml" in names:
            return Kind.PPTX
    return Kind.ZIP


def detect_kind(data: bytes) -> Kind:
    """Kind of a file already in memory."""
    if data.startswith(b"%PDF"):
        return Kind.PDF
    if data.startswith(b"\xff\xd8\xff"):
        return Kind.JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return Kind.PNG
    if data.startswith(_ZIP_MAGIC):
        return _zip_kind(BytesIO(data))
    return Kind.UNKNOWN


def detect_kind_of_stream(file: IO[bytes]) -> Kind:
    """Kind of a (possibly very large) file object, without reading it all into memory."""
    file.seek(0)
    head = file.read(8)
    file.seek(0)
    if head.startswith(_ZIP_MAGIC):
        kind = _zip_kind(file)
        file.seek(0)
        return kind
    return detect_kind(head)


_FORMAT_LABELS = {
    Kind.PDF: "PDF file",
    Kind.DOCX: "Word file",
    Kind.JPEG: "JPG image",
    Kind.PNG: "PNG image",
    Kind.ZIP: "Zip file",
    Kind.XLSX: "Excel file",
    Kind.PPTX: "PowerPoint file",
    Kind.DAMAGED: "Damaged file",
}


def format_label(kind: Kind, filename: str = "") -> str:
    """Short label of a file's format for the user. The name is only used to describe
    content we don't recognise (e.g. "TXT file"); it never decides what is accepted."""
    if kind in _FORMAT_LABELS:
        return _FORMAT_LABELS[kind]
    extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if extension and extension.isalnum() and len(extension) <= 5:
        return f"{extension.upper()} file"
    return "Unknown format"


def media_type(data: bytes) -> str:
    """Content-Type for sending a stored file back to the browser."""
    return MEDIA_TYPES.get(detect_kind(data), "application/octet-stream")
