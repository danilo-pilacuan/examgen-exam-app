"""DTOs del chat (request/response de los endpoints /api/chat)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None


class ChatRequest(BaseModel):
    """Request del chat propio del frontend (POST /api/chat y /api/chat/stream)."""

    message: str = Field(..., min_length=1, description="Mensaje del profesor")
    thread_id: str | None = Field(default=None, description="ID de conversación (nuevo si se omite)")
    subject_id: str | None = None
    subject_name: str | None = None


class ChatResponse(BaseModel):
    """Response del chat no-streaming."""

    thread_id: str
    content: str
    pending_interrupt: dict | None = None
