"""Ports (interfaces) of exam-app: decouple the API from implementations.

Routers consume these ports via `services/`; concrete implementations live
in `infrastructure/` (LangGraph, file system, etc.). This allows testing
exam-app with test doubles.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, Union

from exam_app.schemas.events import SSEEvent


@dataclass
class FileResult:
    """Result of a file upload/processing."""

    file_id: str
    filename: str
    chunks_indexed: int = 0
    indexed: bool = False
    tag: str = ""


class ChatEnginePort(Protocol):
    """Chat engine: orchestrates the conversational graph."""

    async def stream(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> AsyncIterator[Union[SSEEvent, str]]:
        """Runs a turn and emits SSE events (tokens + typed events).

        Raw `str` items are pre-formatted SSE frames (heartbeat comments).
        """
        ...

    async def invoke(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> dict[str, Any]:
        """Runs a full turn (no streaming) and returns the result."""
        ...

    def cancel(self, thread_id: str) -> None:
        """Marks the thread as cancelled (the response is discarded)."""
        ...


class FileServicePort(Protocol):
    """File service: extract → save → index."""

    async def upload(
        self,
        filename: str,
        content: bytes,
        subject_id: str | None = None,
        index: bool = True,
    ) -> FileResult:
        """Processes an uploaded file and (optionally) indexes it as material."""
        ...
