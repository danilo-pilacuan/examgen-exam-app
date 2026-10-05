"""DTOs of the chat (request/response of the /api/chat endpoints)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None


class ChatRequest(BaseModel):
    """Request of the frontend's own chat (POST /api/chat and /api/chat/stream)."""

    message: str = Field(..., min_length=1, description="Message from the professor")
    thread_id: str | None = Field(default=None, description="Conversation ID (new if omitted)")
    subject_id: str | None = None
    subject_name: str | None = None


class ChatResponse(BaseModel):
    """Response of the non-streaming chat."""

    thread_id: str
    content: str
    pending_interrupt: dict | None = None
