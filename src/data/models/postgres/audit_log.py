import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import Action, ActionCategory, PipelineStage
from src.data.models.postgres.base import Base, IdMixin, pg_enum


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"

    # auth-service user ids (no cross-service FK)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    action_category: Mapped[ActionCategory | None] = mapped_column(
        pg_enum(ActionCategory, "action_category"), nullable=True
    )
    actions: Mapped[Action | None] = mapped_column(
        pg_enum(Action, "actions"), nullable=True
    )
    # Pipeline stage change (Created -> Collecting -> ... -> Exported / Partly completed / Failed)
    stage: Mapped[PipelineStage | None] = mapped_column(
        pg_enum(PipelineStage, "pipeline_stage"), nullable=True
    )
    # No FK so logs survive a deleted job.
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    input_data: Mapped[str | None] = mapped_column(String, nullable=True)
    output_data: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # milliseconds
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
