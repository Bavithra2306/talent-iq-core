"""Common types for resume sources."""

from dataclasses import dataclass, field

from src.handlers.http_clients.sourcing.fetcher import PoliteFetcher
from src.utils.file_types import Kind, detect_kind

PDF_MAGIC = b"%PDF"
DOCX_MAGIC = b"PK"


@dataclass
class SourcedFile:
    filename: str
    data: bytes
    source_site: str
    origin: str  # where it came from (URL, profile, dataset row)


@dataclass
class SourceResult:
    files: list[SourcedFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    partial: bool = False  # stopped early (rate limit, quota, some fetches failed)
    note: str | None = None  # plain-language remark shown to the user (not a failure)


def is_resume_file(data: bytes, filename: str = "") -> bool:
    """PDF or DOCX judged by content only; the file name is ignored."""
    return detect_kind(data) in (Kind.PDF, Kind.DOCX)


# One shared fetcher so per-host pacing applies across all sources in a run.
fetcher = PoliteFetcher(min_interval=1.0)
