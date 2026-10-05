"""DTOs for file uploads (own attachments of the frontend)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    """Response of POST /api/files."""

    file_id: str
    filename: str
    subject_id: str | None = None
    chunks_indexed: int = 0
    indexed: bool = False
    tag: str = Field(..., description="<file id=.../> tag to drag into the chat")
