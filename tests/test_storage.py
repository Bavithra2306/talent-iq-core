"""Google Cloud Storage first, the local folder only as the fallback."""

import pytest

from src.data.clients import storage as storage_module
from src.data.clients.storage import LocalStorage, StorageService, make_stored_filename


class FakeGcs:
    def __init__(self, fail=False):
        self.files, self.fail = {}, fail

    def save(self, key, data):
        if self.fail:
            raise ConnectionError("GCS is down")
        self.files[key] = data

    def read(self, key):
        return self.files[key]

    def exists(self, key):
        return key in self.files

    def delete(self, key):
        self.files.pop(key, None)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module.settings, "local_storage_dir", str(tmp_path))
    return StorageService()


def test_without_gcs_files_go_to_the_local_folder(service):
    _, key = service.save("resumes", "cv.pdf", b"pdf")
    assert service.local.exists(key) and service.read(key) == b"pdf"


def test_with_gcs_files_go_only_to_gcs(service):
    service.gcs = FakeGcs()
    _, key = service.save("resumes", "cv.pdf", b"pdf")
    assert service.gcs.exists(key)
    assert not service.local.exists(key)
    assert service.read(key) == b"pdf"


def test_when_gcs_fails_the_file_is_kept_locally(service):
    service.gcs = FakeGcs(fail=True)
    _, key = service.save("resumes", "cv.pdf", b"pdf")
    assert service.local.exists(key) and service.read(key) == b"pdf"


def test_sync_uploads_local_files_missing_in_gcs(service):
    service.gcs = FakeGcs(fail=True)
    _, key = service.save("resumes", "cv.pdf", b"pdf")
    service.gcs.fail = False
    assert service.sync_local_to_gcs() == (1, 0)
    assert service.gcs.read(key) == b"pdf"


def test_stored_names_are_safe(tmp_path):
    name = make_stored_filename("../../etc/my resume (final).pdf")
    assert "/" not in name and " " not in name and name.endswith("my_resume_final_.pdf")
    with pytest.raises(ValueError):
        LocalStorage(str(tmp_path)).read("../outside.txt")
