"""File storage: Google Cloud Storage as the main store, local disk as the fallback.

Keys are relative paths such as "resumes/<uuid>_<name>.pdf"; the same key is used in the
bucket and under the local folder, so the two always line up.

* save:   to GCS (when configured) and always a local copy, so the files can also be
          browsed on the machine (C:\\Development\\TalentIq\\storage in development).
          If GCS fails, the local copy is enough and the error is logged.
* read:   from GCS first; if GCS is not configured or fails, from the local copy.
* delete: from both.

GCS is used when STORAGE_BACKEND=gcs and GCS_BUCKET is set. Credentials come from the
service-account key file in GCS_CREDENTIALS_FILE (or the machine's default credentials).
"""

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

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class GcsStorage:
    """A Google Cloud Storage bucket (the client is created on first use)."""

    def __init__(self, bucket: str, credentials_file: str | None):
        self.bucket_name = bucket
        self.credentials_file = credentials_file
        self._bucket = None

    def _get_bucket(self):
        if self._bucket is None:
            from google.cloud import storage as gcs

            client = (gcs.Client.from_service_account_json(self.credentials_file)
                      if self.credentials_file else gcs.Client())
            self._bucket = client.bucket(self.bucket_name)
        return self._bucket

    def save(self, key: str, data: bytes) -> None:
        self._get_bucket().blob(key).upload_from_string(data)

    def read(self, key: str) -> bytes:
        return self._get_bucket().blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self._get_bucket().blob(key).exists()

    def delete(self, key: str) -> None:
        blob = self._get_bucket().blob(key)
        if blob.exists():
            blob.delete()


class StorageService:
    """GCS first, local disk as the copy and the fallback."""

    def __init__(self):
        self.local = LocalStorage(settings.local_storage_dir)
        self.gcs = (GcsStorage(settings.gcs_bucket, settings.gcs_credentials_file)
                    if settings.storage_backend == "gcs" and settings.gcs_bucket else None)

    @property
    def backend(self) -> str:
        return f"gcs:{settings.gcs_bucket} + local copy" if self.gcs else "local"

    def save(self, folder: str, original_filename: str, data: bytes) -> tuple[str, str]:
        """Store bytes; returns (stored_filename, storage key)."""
        stored_filename = make_stored_filename(original_filename)
        key = f"{folder}/{stored_filename}"
        self.local.save(key, data)
        if self.gcs:
            try:
                self.gcs.save(key, data)
            except Exception:
                logger.exception("GCS upload failed for %s; kept the local copy", key)
        return stored_filename, key

    def read(self, key: str) -> bytes:
        if self.gcs:
            try:
                return self.gcs.read(key)
            except Exception as exc:
                logger.warning("GCS read failed for %s (%s); using the local copy", key, type(exc).__name__)
        return self.local.read(key)

    def delete(self, key: str) -> None:
        self.local.delete(key)
        if self.gcs:
            try:
                self.gcs.delete(key)
            except Exception:
                logger.exception("GCS delete failed for %s", key)

    def sync_local_to_gcs(self) -> tuple[int, int]:
        """Upload local files that are missing in the bucket (e.g. after turning GCS on).
        Returns (uploaded, already there)."""
        if not self.gcs:
            raise RuntimeError("GCS is not configured (STORAGE_BACKEND=gcs and GCS_BUCKET).")
        uploaded = present = 0
        for path in sorted(self.local.root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self.local.root).as_posix()
            if self.gcs.exists(key):
                present += 1
            else:
                self.gcs.save(key, path.read_bytes())
                uploaded += 1
        return uploaded, present


storage = StorageService()
