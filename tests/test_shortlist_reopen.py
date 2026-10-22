"""Top-K counts only entries that can still change; a new resume re-opens a rejection."""

import types
import uuid

from src.constants.enums import CandidateStatus
from src.core.services import resume_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _Result(self._rows)


async def test_a_new_resume_reopens_a_rejected_entry():
    row = types.SimpleNamespace(status=CandidateStatus.REJECTED, shared_to=uuid.uuid4(), notes_by_recruiter=None, updated_by=None)
    user = uuid.uuid4()
    await resume_service._reopen_rejected(_Db([row]), uuid.uuid4(), user)
    assert row.status is CandidateStatus.SOURCED
    assert row.shared_to is None
    assert "Resume updated" in row.notes_by_recruiter
    assert row.updated_by == user
