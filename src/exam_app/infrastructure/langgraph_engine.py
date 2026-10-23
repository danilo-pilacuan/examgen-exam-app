"""ChatEnginePort implementation over the conversational graph.

Runs the agents-backend graph in a thread (to avoid blocking the event
loop), installs the event context (EventBus + thread_id) so tools emit
typed events, and transmits tokens + events through the bus.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any, Union

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
    ) -> AsyncIterator[Union[SSEEvent, str]]:
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
        ticks = 0
        # SSE heartbeat: emit a comment frame every ~15s while nothing else
        # is sent, so proxies (nginx default read timeout is 60s) and the
        # browser know the stream is alive during long LLM/pipeline work.
        PING_EVERY_TICKS = 300  # 300 * 0.05s = 15s
        while not done:
            # Typed agent events (best-effort: if none, keep going).
            try:
                agent_event: AgentEvent = await asyncio.wait_for(
                    subscription.__anext__(), timeout=0.05
                )
                ticks = 0
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

            ticks += 1
            if ticks >= PING_EVERY_TICKS:
                ticks = 0
                # SSE comment frame: ignored by parsers, keeps the connection warm.
                yield ": ping\n\n"
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
    def history(self, thread_id: str) -> list[dict]:
        """Persisted messages of a thread, read from the checkpointer.

        Returns a list of {role, content} dicts (user/assistant only),
        oldest first. Empty list when the thread does not exist.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        try:
            state = conversational_graph.get_state(
                {"configurable": {"thread_id": thread_id}}
            )
        except Exception:  # noqa: BLE001
            return []

        values = getattr(state, "values", None) or {}
        raw_messages = values.get("messages") or []
        out: list[dict] = []
        for msg in raw_messages:
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content")
            if not content:
                continue
            if isinstance(content, list):
                # Multimodal content blocks: keep only the text parts.
                content = " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                ).strip()
            if not content:
                continue
            if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
                role = "user"
            elif isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai":
                role = "assistant"
            else:
                continue  # system/tool messages are not shown in the chat
            out.append({"role": role, "content": content})
        return out

    # ------------------------------------------------------------------
    def cancel(self, thread_id: str) -> None:
        self._bus.cancel(thread_id)


def new_thread_id() -> str:
    """Generates a new thread_id."""
    return str(uuid.uuid4())
