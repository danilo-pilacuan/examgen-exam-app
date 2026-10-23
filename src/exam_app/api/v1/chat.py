"""Own chat endpoints of the frontend.

- POST /api/chat/stream : SSE with typed events (tokens + widgets).
- POST /api/chat        : full turn (no streaming).

The legacy OpenWebUI endpoint (/v1/chat/completions) lives in
`legacy_openai.py` and is not touched.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from core.domain.auth import decode_token, get_current_user, get_db
from core.domain.models import Subject, User

from exam_app.api.deps import get_chat_service
from exam_app.schemas.chat import ChatRequest, ChatResponse
from exam_app.services.chat_engine import ChatService

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _resolve_user(req: ChatRequest, db: OrmSession) -> User:
    """Resolves the authenticated user from the body token (or 401)."""
    if not req.token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(req.token)
    except Exception as e:  # AuthError / jwt errors
        raise HTTPException(status_code=401, detail=str(e)) from e
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def _check_subject_access(db: OrmSession, user: User, subject_id: str | None) -> None:
    """The chat context subject must belong to the professor (or be shared)."""
    if not subject_id:
        return
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")
    subject = db.get(Subject, sid)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    if subject.owner_id is not None and subject.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Subject belongs to another professor")


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    db: OrmSession = Depends(get_db),
):
    """Chat with SSE streaming: tokens + typed tool events."""
    user = _resolve_user(req, db)
    _check_subject_access(db, user, req.subject_id)
    thread_id = req.thread_id or str(uuid.uuid4())

    async def event_stream():
        async for event in service.stream(
            req.message, thread_id, req.subject_id, req.subject_name
        ):
            # Raw strings are pre-formatted SSE frames (heartbeat comments).
            yield event if isinstance(event, str) else event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Thread-Id": thread_id,
        },
    )


@router.get("/history/{thread_id}")
def chat_history(thread_id: str, user: User = Depends(get_current_user)):
    """Returns the persisted messages of a conversation thread.

    Reads the LangGraph checkpointer state (PostgresSaver), so a
    conversation survives logout/login and browser restarts.
    """
    if not thread_id or len(thread_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    from exam_app.api.deps import get_chat_service_sync

    service = get_chat_service_sync()
    messages = service.history(thread_id)
    return {"thread_id": thread_id, "messages": messages}


@router.post("", response_model=ChatResponse)
async def chat_invoke(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    db: OrmSession = Depends(get_db),
):
    """Chat without streaming (a full turn)."""
    user = _resolve_user(req, db)
    _check_subject_access(db, user, req.subject_id)
    thread_id = req.thread_id or str(uuid.uuid4())
    result = await service.invoke(
        req.message, thread_id, req.subject_id, req.subject_name
    )
    return ChatResponse(
        thread_id=result["thread_id"],
        content=result["content"],
    )
