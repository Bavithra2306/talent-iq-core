import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import CandidateStatus
from src.data.models.base import AuditMixin, Base, IdMixin, pg_enum


class MatchingShortlisting(IdMixin, AuditMixin, Base):
    __tablename__ = "matching_shortlisting"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("candidates.id"), nullable=False
    )
    # resume version that was scored
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=True
    )
    status: Mapped[CandidateStatus] = mapped_column(
        pg_enum(CandidateStatus, "candidate_status"),
        default=CandidateStatus.SOURCED,
        server_default="sourced",
    )
    llm_skill_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    llm_role_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    edu_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    exp_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # out of 100
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # skill and role explanations
    explanation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    weights_used: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes_by_recruiter: Mapped[str | None] = mapped_column(Text, nullable=True)
    # auth-service user ids (no cross-service FK)
    shared_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    shared_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        Index("uq_matching_job_candidate", "job_id", "candidate_id", unique=True),
    )
