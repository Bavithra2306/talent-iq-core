import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.postgres.base import AuditMixin, Base, IdMixin


class ResumeExtraction(IdMixin, AuditMixin, Base):
    __tablename__ = "resume_extractions"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False, index=True
    )
    skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    total_experience_years: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), nullable=True
    )
    education: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    current_role: Mapped[str | None] = mapped_column(String, nullable=True)
    roles_and_years: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
