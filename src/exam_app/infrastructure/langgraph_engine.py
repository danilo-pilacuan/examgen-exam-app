"""ChatEnginePort implementation over the conversational graph.

Runs the agents-backend graph in a thread (to avoid blocking the event
loop), installs the event context (EventBus + thread_id) so tools emit
typed events, and transmits tokens + events through the bus.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agents_backend.conversational.graph import conversational_graph
from agents_backend.shared.events import (
    AgentEvent,
    EventBus as AgentEventBus,
    set_event_context,
)
from core.config import settings as core_settings

from exam_app.schemas.events import SSEEvent
from exam_app.services.event_bus import EventBus


class LangGraphEngine:
    """ChatEnginePort implementado con agents-backend (LangGraph)."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    # ------------------------------------------------------------------
    def _prepare_thread(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _initial_state(
        self,
        message: str,
        subject_id: str | None,
        subject_name: str | None,
    ) -> dict[str, Any]:
        return {
            "messages": [{"role": "user", "content": message}],
            "subject_id": subject_id,
            "subject_name": subject_name,
            "pending_item": None,
            "pending_question_id": None,
        }

    # ------------------------------------------------------------------
    async def stream(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Runs the turn in a thread and streams events through the bus.

        The execution thread publishes to `self._bus`; this generator consumes
        the bus and produces the SSE frames until `done`/`error`/`cancelled`.
        """
        agent_bus = AgentEventBus()
        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            # Event context for the tools (contextvar: only visible
            # inside this thread).
            set_event_context(agent_bus, thread_id)
            try:
                return conversational_graph.invoke(
                    self._initial_state(message, subject_id, subject_name),
                    config=self._prepare_thread(thread_id),
                )
            finally:
                set_event_context(None, None)

        task = loop.run_in_executor(None, _run)

        # Consume the agent events while the turn runs.
        subscription = agent_bus.subscribe(thread_id)
        done = False
        while not done:
            # Typed agent events (best-effort: if none, keep going).
            try:
                agent_event: AgentEvent = await asyncio.wait_for(
                    subscription.__anext__(), timeout=0.05
                )
                yield SSEEvent(
                    type=agent_event.type.value,  # type: ignore[arg-type]
                    thread_id=thread_id,
                    payload=agent_event.payload,
                )
                continue
            except (asyncio.TimeoutError, StopAsyncIteration):
                pass

            # Did the turn finish?
            if task.done():
                done = True
                break
            await asyncio.sleep(0.02)

        # Final result: single token with the content (real token streaming
        # arrives with astream_events in stage 2.5).
        try:
            result = task.result()
            messages = result.get("messages", [])
            content = ""
            if messages:
                last = messages[-1]
                content = getattr(last, "content", "") or ""
            # Filter the turn content (the full history comes in
            # result; the last AI message is the response).
            if content:
                yield SSEEvent(
                    type="token", thread_id=thread_id, payload={"delta": content}
                )
        except Exception as e:  # noqa: BLE001
            yield SSEEvent(
                type="error", thread_id=thread_id, payload={"message": str(e)[:300]}
            )
            return

        if self._bus.is_cancelled(thread_id):
            yield SSEEvent(
                type="cancelled", thread_id=thread_id, payload={"message": "cancelled"}
            )
            self._bus.clear_cancel(thread_id)
            return

        yield SSEEvent(type="done", thread_id=thread_id)

    # ------------------------------------------------------------------
    async def invoke(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> dict[str, Any]:
        """Full turn without streaming (for scripts and tests)."""
        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            set_event_context(None, thread_id)
            try:
                return conversational_graph.invoke(
                    self._initial_state(message, subject_id, subject_name),
                    config=self._prepare_thread(thread_id),
                )
            finally:
                set_event_context(None, None)

        result = await loop.run_in_executor(None, _run)
        messages = result.get("messages", [])
        content = ""
        if messages:
            content = getattr(messages[-1], "content", "") or ""
        return {"thread_id": thread_id, "content": content}

    # ------------------------------------------------------------------
    def cancel(self, thread_id: str) -> None:
        self._bus.cancel(thread_id)


def new_thread_id() -> str:
    """Generates a new thread_id."""
    return str(uuid.uuid4())
