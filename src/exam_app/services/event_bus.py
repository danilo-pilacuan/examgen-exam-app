"""Bus de eventos de exam-app: pub/sub entre el motor de chat y SSE/WS.

Cada thread_id tiene su canal. El router SSE se suscribe antes de lanzar el
turno y consume hasta el evento `done`/`error`/`cancelled`.
"""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict, deque

from exam_app.schemas.events import SSEEvent


class EventBus:
    """Pub/sub en memoria de eventos SSE por thread_id (thread-safe)."""

    def __init__(self, max_buffer: int = 512) -> None:
        self._max_buffer = max_buffer
        self._lock = threading.Lock()
        self._queues: dict[str, deque[SSEEvent]] = defaultdict(deque)
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._cancelled: set[str] = set()

    def publish(self, event: SSEEvent) -> None:
        """Publica un evento (thread-safe, best-effort)."""
        with self._lock:
            q = self._queues[event.thread_id]
            q.append(event)
            if len(q) > self._max_buffer:
                q.popleft()
            waiters = list(self._waiters.get(event.thread_id, ()))
        for ev in waiters:
            try:
                ev.set()
            except RuntimeError:
                pass

    def subscribe(self, thread_id: str) -> "EventSubscription":
        return EventSubscription(self, thread_id)

    def cancel(self, thread_id: str) -> None:
        """Marca el thread como cancelado y despierta a los consumidores."""
        with self._lock:
            self._cancelled.add(thread_id)
            waiters = list(self._waiters.get(thread_id, ()))
        for ev in waiters:
            try:
                ev.set()
            except RuntimeError:
                pass

    def is_cancelled(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._cancelled

    def clear_cancel(self, thread_id: str) -> None:
        with self._lock:
            self._cancelled.discard(thread_id)

    # ------------------------------------------------------------------
    def _register_waiter(self, thread_id: str, ev: asyncio.Event) -> None:
        with self._lock:
            self._waiters.setdefault(thread_id, []).append(ev)

    def _unregister_waiter(self, thread_id: str, ev: asyncio.Event) -> None:
        with self._lock:
            waiters = self._waiters.get(thread_id)
            if waiters and ev in waiters:
                waiters.remove(ev)
            if waiters is not None and not waiters:
                self._waiters.pop(thread_id, None)

    def _drain(self, thread_id: str) -> list[SSEEvent]:
        with self._lock:
            q = self._queues.get(thread_id)
            if not q:
                return []
            items = list(q)
            q.clear()
            return items


class EventSubscription:
    """Suscripción asíncrona a los eventos de un thread."""

    def __init__(self, bus: EventBus, thread_id: str) -> None:
        self._bus = bus
        self._thread_id = thread_id
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> SSEEvent:
        if self._closed:
            raise StopAsyncIteration
        ev = asyncio.Event()
        self._bus._register_waiter(self._thread_id, ev)
        try:
            while True:
                items = self._bus._drain(self._thread_id)
                if items:
                    return items[0]
                if self._bus.is_cancelled(self._thread_id):
                    raise StopAsyncIteration
                try:
                    await asyncio.wait_for(ev.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        finally:
            self._bus._unregister_waiter(self._thread_id, ev)

    def close(self) -> None:
        self._closed = True


# Singleton del bus (una instancia por proceso de exam-app).
event_bus = EventBus()
