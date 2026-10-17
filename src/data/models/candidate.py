import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import CandidateStatus
from src.data.models.base import AuditMixin, Base, IdMixin, pg_enum


class Candidate(IdMixin, AuditMixin, Base):
    __tablename__ = "candidates"

    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    contact: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    current_resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False
    )
    current_title: Mapped[str | None] = mapped_column(String, nullable=True)
    # Any field (IT, Medical, Finance, ...); filled in by the LLM during extraction.
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[CandidateStatus | None] = mapped_column(
        pg_enum(CandidateStatus, "candidate_status"), nullable=True
    )
    total_years_experience: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), nullable=True
    )

    __table_args__ = (
        Index("ix_candidates_email", "email"),
        Index("ix_candidates_contact", "contact"),
        Index("ix_candidates_category", "category"),
    )
