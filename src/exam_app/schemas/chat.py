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
    # Bearer JWT (SSE POST cannot always send an Authorization header from
    # the browser client; the token travels in the body instead).
    token: str | None = Field(default=None, description="JWT access token")


class ChatResponse(BaseModel):
    """Response of the non-streaming chat."""

    thread_id: str
    content: str
    pending_interrupt: dict | None = None


class ThreadOut(BaseModel):
    """Metadata of one conversation thread (sidebar list item)."""

    thread_id: str
    title: str
    last_subject_id: str | None = None
    last_subject_name: str | None = None
    message_count: int = 0
    created_at: str
    updated_at: str


class ThreadTitleRequest(BaseModel):
    """Rename of a thread (pinned title, DEC-004 option a)."""

    title: str = Field(..., min_length=1, max_length=200)
