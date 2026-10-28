"""Activity log: hiring manager decisions are counted from each candidate's current status."""

import types
import uuid

from src.constants.enums import Action, CandidateStatus
from src.core.services import shortlist_service


def test_counts_per_job_and_in_total():
    java, python = uuid.uuid4(), uuid.uuid4()
    priya, ravi = uuid.uuid4(), uuid.uuid4()
    rows = [
        (java, "Java Developer", priya, CandidateStatus.APPROVED, 3),
        (java, "Java Developer", priya, CandidateStatus.INTERVIEW_SCHEDULED, 1),
        (java, "Java Developer", ravi, CandidateStatus.REJECTED, 2),
        (python, "Python Developer", priya, CandidateStatus.SHORTLISTED, 4),
    ]

    total, jobs = shortlist_service.summarize_decisions(rows)

    assert (total.shared, total.approved, total.rejected, total.waiting) == (10, 4, 2, 4)
    assert [j.job_title for j in jobs] == ["Java Developer", "Python Developer"]
    assert (jobs[0].approved, jobs[0].rejected, jobs[0].waiting) == (4, 2, 0)
    assert jobs[0].hiring_manager_ids == [priya, ravi]
    assert (jobs[1].shared, jobs[1].waiting) == (4, 4)


def test_nothing_shared_yet():
    total, jobs = shortlist_service.summarize_decisions([])
    assert (total.shared, jobs) == (0, [])


def test_event_detail_reads_candidate_and_comment():
    candidate = uuid.uuid4()
    event = types.SimpleNamespace(actions=Action.CANDIDATE_REJECTED,
                                  input_data=f'{{"candidate_id": "{candidate}", "comment": "Needs Spring"}}')
    assert shortlist_service._event_detail(event) == (candidate, "Needs Spring")
    assert shortlist_service._event_detail(types.SimpleNamespace(input_data="not json")) == (None, None)
