"""Endpoints de chat propios del frontend.

- POST /api/chat/stream : SSE con eventos tipados (tokens + widgets).
- POST /api/chat        : turno completo (sin streaming).

El endpoint legado de OpenWebUI (/v1/chat/completions) vive en
`legacy_openai.py` y no se toca.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from exam_app.api.deps import get_chat_service
from exam_app.schemas.chat import ChatRequest, ChatResponse
from exam_app.services.chat_engine import ChatService

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
):
    """Chat con streaming SSE: tokens + eventos tipados de las tools."""
    thread_id = req.thread_id or str(uuid.uuid4())

    async def event_stream():
        async for event in service.stream(
            req.message, thread_id, req.subject_id, req.subject_name
        ):
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Thread-Id": thread_id,
        },
    )


@router.post("", response_model=ChatResponse)
async def chat_invoke(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
):
    """Chat sin streaming (un turno completo)."""
    thread_id = req.thread_id or str(uuid.uuid4())
    result = await service.invoke(
        req.message, thread_id, req.subject_id, req.subject_name
    )
    return ChatResponse(
        thread_id=result["thread_id"],
        content=result["content"],
    )
