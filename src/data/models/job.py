import uuid

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import JobStatus
from src.data.models.base import AuditMixin, Base, IdMixin, pg_enum


class Job(IdMixin, AuditMixin, Base):
    __tablename__ = "jobs"

    jd_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
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
