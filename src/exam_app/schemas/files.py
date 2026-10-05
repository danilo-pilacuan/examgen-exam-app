"""DTOs de subida de archivos (adjuntos propios del frontend)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    """Response de POST /api/files."""

    file_id: str
    filename: str
    subject_id: str | None = None
    chunks_indexed: int = 0
    indexed: bool = False
    tag: str = Field(..., description="Tag <file id=.../> para arrastrar al chat")
