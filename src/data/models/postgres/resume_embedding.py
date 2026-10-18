import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import EmbeddingKind
from src.data.models.postgres.base import EMBEDDING_DIM, AuditMixin, Base, IdMixin, pg_enum


class ResumeEmbedding(IdMixin, AuditMixin, Base):
    __tablename__ = "resume_embeddings"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("candidates.id"), nullable=False, index=True
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=False, index=True
    )
    kind: Mapped[EmbeddingKind] = mapped_column(
        pg_enum(EmbeddingKind, "embedding_kind"), nullable=False
    )
    vector: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
