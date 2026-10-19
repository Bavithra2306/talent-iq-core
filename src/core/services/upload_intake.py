"""Turns an upload request into a flat list of files for the resume pipeline.

Two kinds of upload are allowed, one at a time:

* separate files (also what a dragged folder becomes), at most 150;
* exactly one zip file, with any folders inside, holding at most 150 files.

Everything is judged by the file's content (magic bytes), never by its name. Files that
are not a PDF or DOCX are skipped one by one with a reason; the upload itself goes on.
Limits (see settings): file size, file count and total size, so a huge or booby-trapped
zip cannot fill the disk or memory.
"""

import asyncio
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import IO

from fastapi import UploadFile

from src.config.settings import settings
from src.core.exceptions import BadRequestError, FileTooLargeError
from src.core.services.resume_service import UploadedFile
from src.utils.file_types import LABELS, Kind, detect_kind, detect_kind_of_stream

logger = logging.getLogger("talentiq.upload")

_MB = 1024 * 1024
_ACCEPTED = {Kind.PDF, Kind.DOCX}
_JUNK_NAMES = {"thumbs.db", "desktop.ini"}


@dataclass
class Intake:
    uploads: list[UploadedFile] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def _too_big_reason() -> str:
    return f"Larger than {settings.max_upload_bytes // _MB} MB."


def _wrong_type_reason(kind: Kind, *, nested_zip: bool = False) -> str:
    if nested_zip:
        return "Zip files inside a zip are not accepted."
    seen = "" if kind is Kind.UNKNOWN else f" (this is {LABELS[kind]})"
    return f"Only PDF and DOCX files are accepted{seen}."


def _is_junk(path: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    name = parts[-1].lower() if parts else ""
    return (
        any(p.startswith("__MACOSX") for p in parts)
        or name.startswith(".")
        or name.startswith("~$")
        or name in _JUNK_NAMES
    )


def _is_unsafe(path: str) -> bool:
    posix = path.replace("\\", "/")
    return posix.startswith("/") or ".." in PurePosixPath(posix).parts or ":" in posix.split("/")[0]


def expand_zip(file: IO[bytes]) -> Intake:
    """Read every file in a zip (any folder depth) and judge each one by its content."""
    try:
        archive = zipfile.ZipFile(file)
    except zipfile.BadZipFile as exc:
        raise BadRequestError("The zip file is damaged and could not be opened.") from exc

    out = Intake()
    with archive:
        entries = [i for i in archive.infolist() if not i.is_dir() and not _is_junk(i.filename)]
        if not entries:
            raise BadRequestError("The zip file has no files in it.")
        if len(entries) > settings.max_resumes_per_upload:
            raise FileTooLargeError(
                f"The zip has {len(entries)} files. At most {settings.max_resumes_per_upload} "
                "files per upload; please split it into smaller zips."
            )
        if sum(i.file_size for i in entries) > settings.max_upload_total_bytes:
            raise FileTooLargeError(
                f"The files inside the zip add up to more than "
                f"{settings.max_upload_total_bytes // _MB} MB. Please split it into smaller zips."
            )

        for info in entries:
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            if _is_unsafe(info.filename):
                out.rejected.append({"filename": name, "reason": "The file path inside the zip is not safe."})
            elif info.flag_bits & 0x1:
                out.rejected.append({"filename": name, "reason": "The file is password protected."})
            elif info.file_size == 0:
                out.rejected.append({"filename": name, "reason": "The file is empty."})
            elif info.file_size > settings.max_upload_bytes:
                out.rejected.append({"filename": name, "reason": _too_big_reason()})
            else:
                try:
                    with archive.open(info) as handle:
                        data = handle.read(settings.max_upload_bytes + 1)
                except Exception:
                    logger.warning("could not read %s from zip", info.filename, exc_info=True)
                    out.rejected.append({"filename": name, "reason": "The file could not be read from the zip."})
                    continue
                kind = detect_kind(data)
                if kind in _ACCEPTED:
                    out.uploads.append(UploadedFile(filename=name, data=data))
                else:
                    out.rejected.append({"filename": name,
                                         "reason": _wrong_type_reason(kind, nested_zip=kind is Kind.ZIP)})
    return out


async def collect(files: list[UploadFile]) -> Intake:
    """Validate the request as a whole and return the files to process."""
    if not files:
        raise BadRequestError("Please choose at least one file.")
    if sum(f.size or 0 for f in files) > settings.max_upload_total_bytes:
        raise FileTooLargeError(
            f"This upload is larger than {settings.max_upload_total_bytes // _MB} MB. "
            "Please upload fewer files at a time."
        )

    kinds = [detect_kind_of_stream(f.file) for f in files]
    if len(files) == 1 and kinds[0] is Kind.DAMAGED:
        raise BadRequestError("The zip file is damaged and could not be opened.")
    if Kind.ZIP in kinds:
        if len(files) > 1:
            raise BadRequestError("Upload either one zip file or separate files, not both.")
        return await asyncio.to_thread(expand_zip, files[0].file)

    if len(files) > settings.max_resumes_per_upload:
        raise FileTooLargeError(f"At most {settings.max_resumes_per_upload} files per upload.")

    out = Intake()
    for upload, kind in zip(files, kinds, strict=True):
        name = upload.filename or "resume"
        if not upload.size:
            out.rejected.append({"filename": name, "reason": "The file is empty."})
        elif upload.size > settings.max_upload_bytes:
            out.rejected.append({"filename": name, "reason": _too_big_reason()})
        elif kind not in _ACCEPTED:
            out.rejected.append({"filename": name, "reason": _wrong_type_reason(kind)})
        else:
            out.uploads.append(UploadedFile(filename=name, data=await upload.read()))
    return out
