"""WebSocket /ws: bus de notificaciones y cancelación.

Protocolo (JSON):
  - Cliente → servidor: {"type": "cancel", "thread_id": "..."}
  - Servidor → cliente: eventos SSE (mismo schema) + acks.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from exam_app.api.deps import get_chat_service, get_event_bus
from exam_app.services.event_bus import EventBus
from exam_app.services.chat_engine import ChatService

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Canal push del frontend: notificaciones y cancelación."""
    await websocket.accept()
    bus: EventBus = get_event_bus()
    service: ChatService = get_chat_service()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"v": 1, "type": "error", "thread_id": "", "payload": {"message": "JSON inválido"}}
                )
                continue

            msg_type = msg.get("type")
            thread_id = msg.get("thread_id", "")

            if msg_type == "cancel":
                service.cancel(thread_id)
                await websocket.send_json(
                    {
                        "v": 1,
                        "type": "cancelled",
                        "thread_id": thread_id,
                        "payload": {"message": "cancelación solicitada"},
                    }
                )
            elif msg_type == "ping":
                await websocket.send_json({"v": 1, "type": "pong", "thread_id": thread_id})
            else:
                await websocket.send_json(
                    {
                        "v": 1,
                        "type": "error",
                        "thread_id": thread_id,
                        "payload": {"message": f"tipo no soportado: {msg_type}"},
                    }
                )
    except WebSocketDisconnect:
        return
