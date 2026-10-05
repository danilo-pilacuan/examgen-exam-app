"""Implementación de ChatEnginePort sobre el grafo conversacional.

Ejecuta el grafo de agents-backend en un hilo (para no bloquear el event
loop), instala el contexto de eventos (EventBus + thread_id) para que las
tools emitan eventos tipados, y transmite tokens + eventos por el bus.
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
        """Ejecuta el turno en un hilo y transmite eventos por el bus.

        El hilo de ejecución publica en `self._bus`; este generador consume
        del bus y produce los frames SSE hasta `done`/`error`/`cancelled`.
        """
        agent_bus = AgentEventBus()
        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            # Contexto de eventos para las tools (contextvar: solo visible
            # dentro de este hilo).
            set_event_context(agent_bus, thread_id)
            try:
                return conversational_graph.invoke(
                    self._initial_state(message, subject_id, subject_name),
                    config=self._prepare_thread(thread_id),
                )
            finally:
                set_event_context(None, None)

        task = loop.run_in_executor(None, _run)

        # Consumir los eventos del agente mientras el turno corre.
        subscription = agent_bus.subscribe(thread_id)
        done = False
        while not done:
            # Eventos tipados del agente (best-effort: si no hay, sigue).
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

            # ¿Terminó el turno?
            if task.done():
                done = True
                break
            await asyncio.sleep(0.02)

        # Resultado final: token único con el contenido (el streaming real
        # de tokens llega con astream_events en la etapa 2.5).
        try:
            result = task.result()
            messages = result.get("messages", [])
            content = ""
            if messages:
                last = messages[-1]
                content = getattr(last, "content", "") or ""
            # Filtrar el contenido del turno (el historial completo viene en
            # result; el último mensaje AI es la respuesta).
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
                type="cancelled", thread_id=thread_id, payload={"message": "cancelado"}
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
        """Turno completo sin streaming (para scripts y pruebas)."""
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
    """Genera un thread_id nuevo."""
    return str(uuid.uuid4())
