import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.constants.enums import FileType
from src.schema.base import ORMModel


class DocumentBase(BaseModel):
    source_type: str | None = Field(default=None, description="uploaded / sourced")
    source_site: str | None = Field(
        default=None, description="github / kaggle / huggingface / stackexchange / url"
    )
    document_type: str | None = Field(default=None, description="resume / jd")
    original_filename: str | None = None
    stored_filename: str | None = None
    file_type: FileType | None = None
    file_size: int | None = Field(default=None, ge=0)


class DocumentCreate(DocumentBase):
    uploaded_by: uuid.UUID
    file_hash: str = Field(min_length=64, max_length=64, description="SHA-256 hex")
    file_path: str = Field(description="storage key, not a full path or URL")
    version: int = 1


class DocumentUpdate(BaseModel):
    source_type: str | None = None
    document_type: str | None = None
    original_filename: str | None = None
    stored_filename: str | None = None
    version: int | None = None


class DocumentRead(DocumentBase, ORMModel):
    id: uuid.UUID
    uploaded_by: uuid.UUID
    file_hash: str
    file_path: str
    version: int
    created_at: datetime
