"""Versioned SSE/WS event schema (contract with the frontend).

Mirror of `agents_backend.shared.events` at the API boundary: exam-app
forwards agent events without knowing their semantics, but validates the
envelope shape (v, type, thread_id).

See docs/arquitectura-objetivo.md §5.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

EventType = Literal[
    "token",
    "tool_start",
    "tool_end",
    "question_generated",
    "questions_listed",
    "artifact_ready",
    "material_indexed",
    "question_saved",
    "subject_registered",
    "cancelled",
    "done",
    "error",
]


class SSEEvent(BaseModel):
    """Standard envelope of an SSE/WS event."""

    v: int = SCHEMA_VERSION
    type: EventType
    thread_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Serializa como frame SSE (`event: <type>\\ndata: <json>\\n\\n`)."""
        import json

        data = json.dumps(self.model_dump(), ensure_ascii=False)
        return f"event: {self.type}\ndata: {data}\n\n"


class TokenEvent(SSEEvent):
    type: Literal["token"] = "token"
    payload: dict[str, Any] = Field(default_factory=lambda: {"delta": ""})


class DoneEvent(SSEEvent):
    type: Literal["done"] = "done"


class ErrorEvent(SSEEvent):
    type: Literal["error"] = "error"
