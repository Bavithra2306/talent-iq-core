import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models.postgres.base import AuditMixin, Base, IdMixin


class ScoringConfig(IdMixin, AuditMixin, Base):
    __tablename__ = "scoring_config"

    # one config per job
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id"), nullable=False, unique=True
    )
    skill_pct: Mapped[int] = mapped_column(Integer, default=40, server_default="40")
    role_pct: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    exp_pct: Mapped[int] = mapped_column(Integer, default=25, server_default="25")
    edu_pct: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    total_pct: Mapped[int] = mapped_column(Integer, default=100, server_default="100")

    __table_args__ = (
        CheckConstraint(
            "skill_pct + role_pct + exp_pct + edu_pct = 100 AND total_pct = 100",
            name="ck_scoring_config_total_100",
        ),
    )
