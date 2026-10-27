"""Own chat endpoints of the frontend.

- POST /api/chat/stream : SSE with typed events (tokens + widgets).
- POST /api/chat        : full turn (no streaming).

The legacy OpenWebUI endpoint (/v1/chat/completions) lives in
`legacy_openai.py` and is not touched.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from core.domain.auth import decode_token, get_current_user, get_db
from core.domain.models import Subject, User

from exam_app.api.deps import get_chat_service
from exam_app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ThreadOut,
    ThreadTitleRequest,
)
from exam_app.services.chat_engine import ChatService
from exam_app.services.threads_service import (
    ThreadAccessError,
    ThreadNotFoundError,
    delete_thread,
    list_user_threads,
    record_turn,
    register_or_adopt,
    rename_thread,
    require_owned_thread,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _thread_http_error(e: Exception) -> HTTPException:
    """Maps thread registry domain errors to HTTP statuses."""
    if isinstance(e, ThreadAccessError):
        return HTTPException(status_code=403, detail="Thread belongs to another user")
    if isinstance(e, ThreadNotFoundError):
        return HTTPException(status_code=404, detail="Thread not found")
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    raise e


def _resolve_user(req: ChatRequest, db: OrmSession, request: Request | None = None) -> User:
    """Resolves the authenticated user from the body token (or 401).

    The body token is the primary source (SSE clients cannot always send
    headers); the `Authorization: Bearer` header is accepted as a fallback
    for REST callers (`apiFetch` always injects it).
    """
    token = req.token
    if not token and request is not None:
        auth_header = request.headers.get("Authorization") or ""
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip() or None
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
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


def _check_thread_access(db: OrmSession, user: User, thread_id: str | None) -> None:
    """The thread must belong to the caller (BUG-022 fix; DEC-004 a).

    Unknown thread_ids pass here (a new conversation starts with a fresh
    id) and get registered for the calling user during the turn; a
    thread_id already registered to ANOTHER user is rejected with 403 at
    `register_or_adopt` (it never silently reassigns ownership).
    """
    if not thread_id:
        return
    if len(thread_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    # get_owned_thread returns the row only when it belongs to the caller;
    # a row owned by someone else behaves exactly like an unknown id here
    # (None), and the registry row for new conversations is created during
    # the turn. Foreign REGISTERED threads are therefore rejected with 403
    # at registration time, not probed further.


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    service: ChatService = Depends(get_chat_service),
    db: OrmSession = Depends(get_db),
):
    """Chat with SSE streaming: tokens + typed tool events."""
    user = _resolve_user(req, db, request)
    _check_subject_access(db, user, req.subject_id)
    _check_thread_access(db, user, req.thread_id)
    thread_id = req.thread_id or str(uuid.uuid4())

    # Register/adopt the thread so the listing shows it from any device.
    # Title is pinned to the first user message (DEC-004).
    try:
        register_or_adopt(db, user.id, thread_id, req.message[:200])
    except (ThreadAccessError, ThreadNotFoundError, ValueError) as e:
        raise _thread_http_error(e) from e

    # Badge metadata: the turn adds 1 user message (+1 assistant at the end).
    def _record(count: int) -> None:
        try:
            record_turn(db, user.id, thread_id, req.subject_id, req.subject_name, count)
        except (ThreadAccessError, ThreadNotFoundError, ValueError):
            pass  # badge bookkeeping must never break the stream

    async def event_stream():
        async for event in service.stream(
            req.message, thread_id, req.subject_id, req.subject_name
        ):
            # Raw strings are pre-formatted SSE frames (heartbeat comments).
            yield event if isinstance(event, str) else event.to_sse()
        _record(2)

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
def chat_history(
    thread_id: str,
    db: OrmSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns the persisted messages of a conversation thread.

    Reads the LangGraph checkpointer state (PostgresSaver), so a
    conversation survives logout/login and browser restarts.
    """
    if not thread_id or len(thread_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid thread_id")

    # BUG-022 fix: only the owner can read the conversation.
    try:
        require_owned_thread(db, user.id, thread_id)
    except (ThreadAccessError, ThreadNotFoundError) as e:
        raise _thread_http_error(e) from e

    from exam_app.api.deps import get_chat_service_sync

    service = get_chat_service_sync()
    messages = service.history(thread_id)
    return {"thread_id": thread_id, "messages": messages}


@router.get("/threads", response_model=list[ThreadOut])
def chat_threads(
    db: OrmSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lists the caller's conversations (BUG-023 fix; replaces localStorage).

    Most recently active first. Includes badge metadata: pinned title,
    last subject used and message count (DEC-004 option a).
    """
    rows = list_user_threads(db, user.id)
    return [
        ThreadOut(
            thread_id=r.thread_id,
            title=r.title,
            last_subject_id=str(r.last_subject_id) if r.last_subject_id else None,
            last_subject_name=r.last_subject_name,
            message_count=r.message_count,
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
        )
        for r in rows
    ]


@router.patch("/threads/{thread_id}", response_model=ThreadOut)
def chat_thread_rename(
    thread_id: str,
    req: ThreadTitleRequest,
    db: OrmSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Renames a conversation (pinned title; owner only)."""
    if len(thread_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    try:
        row = rename_thread(db, user.id, thread_id, req.title)
    except (ThreadAccessError, ThreadNotFoundError, ValueError) as e:
        raise _thread_http_error(e) from e
    return ThreadOut(
        thread_id=row.thread_id,
        title=row.title,
        last_subject_id=str(row.last_subject_id) if row.last_subject_id else None,
        last_subject_name=row.last_subject_name,
        message_count=row.message_count,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


@router.delete("/threads/{thread_id}", status_code=204)
def chat_thread_delete(
    thread_id: str,
    db: OrmSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deletes a conversation from the registry (owner only)."""
    if len(thread_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    try:
        delete_thread(db, user.id, thread_id)
    except (ThreadAccessError, ThreadNotFoundError) as e:
        raise _thread_http_error(e) from e
    return None


@router.post("", response_model=ChatResponse)
async def chat_invoke(
    req: ChatRequest,
    request: Request,
    service: ChatService = Depends(get_chat_service),
    db: OrmSession = Depends(get_db),
):
    """Chat without streaming (a full turn)."""
    user = _resolve_user(req, db, request)
    _check_subject_access(db, user, req.subject_id)
    _check_thread_access(db, user, req.thread_id)
    thread_id = req.thread_id or str(uuid.uuid4())
    try:
        register_or_adopt(db, user.id, thread_id, req.message[:200])
    except (ThreadAccessError, ThreadNotFoundError, ValueError) as e:
        raise _thread_http_error(e) from e
    result = await service.invoke(
        req.message, thread_id, req.subject_id, req.subject_name
    )
    try:
        record_turn(db, user.id, thread_id, req.subject_id, req.subject_name, 2)
    except (ThreadAccessError, ThreadNotFoundError, ValueError):
        pass  # badge bookkeeping must never break the response
    return ChatResponse(
        thread_id=result["thread_id"],
        content=result["content"],
    )
