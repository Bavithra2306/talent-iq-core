"""A file that can't be read goes to the review queue instead of staying in "Retry failed" forever."""

import types
import uuid

from src.core.services import resume_service


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


def _setup(monkeypatch, document):
    async def no_log(**_kwargs):
        pass

    async def get_document(_db, _document_id):
        return document

    monkeypatch.setattr(resume_service, "log_event", no_log)
    monkeypatch.setattr(resume_service, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(resume_service.resume_repository, "get_document", get_document)


async def test_an_unreadable_file_is_counted_failed_and_moved_to_review(monkeypatch):
    document = types.SimpleNamespace(document_type=resume_service.DOC_TYPE_RESUME)
    _setup(monkeypatch, document)
    state = resume_service.BatchProgress(total=1)
    item = resume_service.BatchItem(uuid.uuid4(), "Resume_cut_off.pdf", "resumes/x.pdf")

    await resume_service.fail_item("batch", uuid.uuid4(), state, item, "The file could not be read.", review_file=True)

    assert document.document_type == resume_service.DOC_TYPE_REVIEW
    assert (state.failed, state.needs_review) == (1, 0)


async def test_an_ai_failure_stays_retryable(monkeypatch):
    document = types.SimpleNamespace(document_type=resume_service.DOC_TYPE_RESUME)
    _setup(monkeypatch, document)
    state = resume_service.BatchProgress(total=1)
    item = resume_service.BatchItem(uuid.uuid4(), "resume.pdf", "resumes/y.pdf")

    await resume_service.fail_item("batch", uuid.uuid4(), state, item, "The AI service could not read this resume.")

    assert document.document_type == resume_service.DOC_TYPE_RESUME
    assert state.failed == 1
