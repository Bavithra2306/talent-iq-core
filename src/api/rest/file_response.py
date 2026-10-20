"""One way to send a stored file to the browser: inline preview or download."""

from fastapi import Response
from fastapi.responses import HTMLResponse

from src.utils.file_types import Kind, detect_kind, media_type
from src.utils.preview import docx_to_html


def file_response(content: bytes, filename: str, download: bool = False) -> Response:
    """PDFs and images open in the browser; Word files open as a web-page preview.

    ``download=True`` always returns the original file as an attachment.
    """
    name = (filename or "file").replace('"', "")
    if download:
        return Response(content=content, media_type=media_type(content),
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    if detect_kind(content) is Kind.DOCX:
        return HTMLResponse(docx_to_html(content, name))
    return Response(content=content, media_type=media_type(content),
                    headers={"Content-Disposition": f'inline; filename="{name}"'})
