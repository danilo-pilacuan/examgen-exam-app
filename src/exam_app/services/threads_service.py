"""Thread registry service: server-side conversation metadata per user.

Implements DEC-004 option (a):
  - Every thread belongs to exactly one user (`chat_threads.user_id`).
  - The listing endpoint returns only the caller's threads (fixes BUG-023:
    the sidebar no longer depends on localStorage).
  - Ownership validation raises 403 for foreign threads (fixes BUG-022).
  - Badge metadata: pinned title, last subject used, message count.

Checkpoint threads created before this table exist are adopted lazily: the
first authenticated turn that touches an unknown thread_id registers it for
the calling user.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session as OrmSession

from core.domain.models import ChatThread

# Hard limits (threads are user-created; keep rows small and predictable).
MAX_TITLE_LENGTH = 200
MAX_MESSAGE_COUNT = 1_000_000
MAX_THREADS_PER_USER = 1000


class ThreadAccessError(Exception):
    """The thread exists but belongs to another user (maps to 403)."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"Thread {thread_id} belongs to another user")


class ThreadNotFoundError(Exception):
    """The thread has no registry row and no checkpoint content."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"Thread {thread_id} does not exist")


def get_owned_thread(db: OrmSession, user_id: uuid.UUID, thread_id: str) -> ChatThread | None:
    """Returns the thread row when it exists and belongs to the user."""
    row = db.get(ChatThread, thread_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def require_owned_thread(db: OrmSession, user_id: uuid.UUID, thread_id: str) -> ChatThread:
    """Like `get_owned_thread` but raises the domain errors (403/404)."""
    row = db.get(ChatThread, thread_id)
    if row is None:
        raise ThreadNotFoundError(thread_id)
    if row.user_id != user_id:
        raise ThreadAccessError(thread_id)
    return row


def register_thread(
    db: OrmSession,
    user_id: uuid.UUID,
    thread_id: str,
    title: str,
) -> ChatThread:
    """Creates the registry row of a new thread (idempotent).

    Used when the user starts a conversation or adopts an existing
    checkpoint thread. The first turn sets the pinned title; later turns do
    not overwrite it.
    """
    row = db.get(ChatThread, thread_id)
    if row is not None:
        if row.user_id != user_id:
            raise ThreadAccessError(thread_id)
        return row
    total = (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user_id)
        .count()
    )
    if total >= MAX_THREADS_PER_USER:
        raise ValueError("Thread limit reached")
    row = ChatThread(
        thread_id=thread_id[:64],
        user_id=user_id,
        title=title[:MAX_TITLE_LENGTH],
        message_count=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def register_or_adopt(
    db: OrmSession,
    user_id: uuid.UUID,
    thread_id: str,
    title: str,
) -> ChatThread:
    """Registers a new thread or adopts an ownerless legacy thread.

    Legacy adoption (rounds 1-6 data): threads that only exist in the
    checkpointer have no row; on the owner's first turn after this change
    the row is created for the calling user. A row already claimed by
    another user raises 403 (never silently reassigns).
    """
    return register_thread(db, user_id, thread_id, title)


def record_turn(
    db: OrmSession,
    user_id: uuid.UUID,
    thread_id: str,
    subject_id: str | None,
    subject_name: str | None,
    added_messages: int,
) -> ChatThread:
    """Updates badge metadata after a chat turn (last subject, count)."""
    row = require_owned_thread(db, user_id, thread_id)
    if subject_id:
        try:
            row.last_subject_id = uuid.UUID(subject_id)
        except ValueError:
            row.last_subject_id = None
    if subject_name:
        row.last_subject_name = subject_name[:MAX_TITLE_LENGTH]
    row.message_count = min(row.message_count + max(0, added_messages), MAX_MESSAGE_COUNT)
    db.commit()
    db.refresh(row)
    return row


def list_user_threads(db: OrmSession, user_id: uuid.UUID) -> list[ChatThread]:
    """All threads of the user, most recently active first."""
    return (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user_id)
        .order_by(ChatThread.updated_at.desc(), ChatThread.created_at.desc())
        .all()
    )


def rename_thread(
    db: OrmSession, user_id: uuid.UUID, thread_id: str, title: str
) -> ChatThread:
    """Pinned rename (only the owner can rename)."""
    row = require_owned_thread(db, user_id, thread_id)
    clean = title.strip()
    if not clean:
        raise ValueError("Title cannot be empty")
    row.title = clean[:MAX_TITLE_LENGTH]
    db.commit()
    db.refresh(row)
    return row


def delete_thread(db: OrmSession, user_id: uuid.UUID, thread_id: str) -> None:
    """Removes the registry row (only the owner can delete).

    Checkpointer content is left as-is (out of scope of this table; the
    checkpoint orphan cleanup is tracked separately in BUG-023 notes).
    """
    row = require_owned_thread(db, user_id, thread_id)
    db.delete(row)
    db.commit()
