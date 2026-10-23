"""Database access for the shortlist export."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.enums import CandidateStatus
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.matching_shortlisting import MatchingShortlisting


async def find_scored_rows(
    db: AsyncSession, job_id: uuid.UUID, *, shared_to: uuid.UUID | None = None,
    statuses: tuple[CandidateStatus, ...] | None = None, ranked_only: bool = False,
) -> list[tuple[MatchingShortlisting, Candidate]]:
    """(result row, candidate) of everyone scored for a job, limited to what was shared with one
    person, to some statuses, or to this run's ranked candidates."""
    query = (select(MatchingShortlisting, Candidate)
             .join(Candidate, Candidate.id == MatchingShortlisting.candidate_id)
             .where(MatchingShortlisting.job_id == job_id, MatchingShortlisting.total_score.is_not(None)))
    if shared_to is not None:
        query = query.where(MatchingShortlisting.shared_to == shared_to)
    elif statuses is not None:
        query = query.where(MatchingShortlisting.status.in_(statuses))
    elif ranked_only:
        query = query.where(MatchingShortlisting.rank.is_not(None))
    return [tuple(r) for r in (await db.execute(query)).all()]
