"""Chat service: orchestrates the engine and exposes business logic.

Routers consume this service (never import from agents-backend or core
directly).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from exam_app.domain.ports import ChatEnginePort
from exam_app.schemas.events import SSEEvent
from exam_app.services.event_bus import EventBus


class ChatService:
    """Chat use case: converse with the agent (streaming or invoke)."""

    def __init__(self, engine: ChatEnginePort, bus: EventBus) -> None:
        self._engine = engine
        self._bus = bus

    async def stream(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> AsyncIterator[SSEEvent]:
        self._bus.clear_cancel(thread_id)
        async for event in self._engine.stream(
            message, thread_id, subject_id, subject_name
        ):
            yield event

    async def invoke(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> dict:
        return await self._engine.invoke(message, thread_id, subject_id, subject_name)

    def cancel(self, thread_id: str) -> None:
        self._engine.cancel(thread_id)
