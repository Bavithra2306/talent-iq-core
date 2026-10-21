import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import JobStatus
from src.data.models.postgres.base import AuditMixin, Base, IdMixin, pg_enum


class Job(IdMixin, AuditMixin, Base):
    __tablename__ = "jobs"

    jd_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Short role / designation, e.g. "SDE II" (from the JD, editable).
    role_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    experience_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    education: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        default=JobStatus.OPEN,
        server_default="open",
    )
    # required skills list
    skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    nice_to_have_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Soft delete: the job is hidden everywhere but stays in the database.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
