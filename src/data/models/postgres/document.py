import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.constants.enums import FileType
from src.data.models.postgres.base import Base, IdMixin, pg_enum


class Document(IdMixin, Base):
    __tablename__ = "documents"

    # auth-service user id (no FK across services)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # 'uploaded' / 'scraped'
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # where a sourced resume came from (github / kaggle / huggingface / stackexchange / url); None for uploads
    source_site: Mapped[str | None] = mapped_column(String, nullable=True)
    # 'resume' / 'jd'
    document_type: Mapped[str | None] = mapped_column(String, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    # "<uuid>_<original_filename>" (no hashing)
    stored_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    # SHA-256 hex digest
    file_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_type: Mapped[FileType | None] = mapped_column(
        pg_enum(FileType, "file_type"), nullable=True
    )
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # storage key, not a full path or URL
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # the upload or sourcing batch that added this file (None for files added before batches were recorded)
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
