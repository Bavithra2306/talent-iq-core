"""File storage: GCS (planned) with local-disk fallback. Keys are relative paths like "jd/<uuid>_<name>.pdf"."""

import logging
import re
import uuid
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger("talentiq.storage")


def make_stored_filename(original_filename: str) -> str:
    """'<uuid>_<original filename>' with path parts and unsafe characters removed."""
    name = Path(original_filename or "file").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"
    return f"{uuid.uuid4()}_{name[:150]}"


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key.")
        return path

    def save(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class StorageService:
    """Writes to the configured backend; falls back to local disk if it fails."""

    def __init__(self):
        self.local = LocalStorage(settings.local_storage_dir)

    def save(self, folder: str, original_filename: str, data: bytes) -> tuple[str, str]:
        """Store bytes; returns (stored_filename, storage key)."""
        stored_filename = make_stored_filename(original_filename)
        key = f"{folder}/{stored_filename}"
        if settings.storage_backend == "gcs":
            # GCS upload is not implemented yet; local disk is used meanwhile.
            logger.warning("GCS backend not available yet; storing %s locally", key)
        self.local.save(key, data)
        return stored_filename, key

    def read(self, key: str) -> bytes:
        return self.local.read(key)

    def delete(self, key: str) -> None:
        self.local.delete(key)


storage = StorageService()
