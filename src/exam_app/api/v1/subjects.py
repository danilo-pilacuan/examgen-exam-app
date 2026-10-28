"""REST endpoints for subjects and the question bank.

- GET    /subjects                          : list subjects
- POST   /subjects                          : register subject
- GET    /subjects/{id}/questions           : question bank (filters)
- PUT    /questions/{id}/verification       : human verification (HITL)
- DELETE /questions/{id}                    : delete question (guardrail)

Bank write endpoints go through `core.domain.bank.service`
(never direct SQL) to respect the human-verification guardrails.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy import select

from core.domain.auth import get_current_user, get_db
from core.domain.models import Subject, GeneratedQuestion, User, LLMResponse
from core.config import settings
from core.domain.bank.service import (
    SecurityError,
    list_questions,
    set_human_verification,
    delete_question,
)
from core.infrastructure.db.connection import SessionLocal

from exam_app.schemas.bank import (
    BankExport,
    BankExportQuestion,
    QuestionFilters,
    QuestionOut,
    SubjectCreate,
    SubjectOut,
    VerificationRequest,
)

router = APIRouter(tags=["subjects"])


def _build_export_question(q: GeneratedQuestion, prompt: str | None, thinking: str | None, model: str | None) -> BankExportQuestion:
    """Maps one bank row to the export DTO with its LLM audit fields."""
    return BankExportQuestion(
        id=str(q.id),
        topic=q.topic,
        subtopic=q.subtopic,
        bloom_level=q.bloom_level,
        difficulty=q.difficulty,
        question_type=q.question_type,
        question_text=q.question_text,
        options=q.options,
        expected_answer=q.expected_answer,
        solution_explanation=q.solution_explanation,
        points=q.points,
        verified_by_human=q.verified_by_human,
        verified_by_ai=q.verified_by_ai,
        ai_review_notes=q.ai_review_notes,
        ai_review_priority=q.ai_review_priority,
        source=q.source,
        created_at=str(q.created_at) if q.created_at else None,
        prompt=prompt,
        thinking=thinking,
        generation_model=model,
    )


def _get_owned_subject(db: OrmSession, subject_id: str, user: User) -> Subject:
    """Resolves a subject the user may access (own or legacy/shared)."""
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")
    subject = db.get(Subject, sid)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    if subject.owner_id is not None and subject.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Subject belongs to another professor")
    return subject


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Lists the subjects of the authenticated professor.

    Includes legacy/shared subjects (owner_id IS NULL) so existing data
    keeps working after the multi-tenant migration.
    """
    subjects = (
        db.query(Subject)
        .filter(or_(Subject.owner_id == user.id, Subject.owner_id.is_(None)))
        .order_by(Subject.name)
        .all()
    )
    return [
        SubjectOut(id=str(s.id), name=s.name)
        for s in subjects
    ]


@router.post("/subjects", response_model=SubjectOut)
def create_subject(
    req: SubjectCreate,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Registers a subject owned by the authenticated professor.

    Returns the existing one (without duplicating) if the professor already
    has a subject with that name.
    """
    subject = (
        db.query(Subject)
        .filter(
            Subject.name.ilike(req.name),
            or_(Subject.owner_id == user.id, Subject.owner_id.is_(None)),
        )
        .first()
    )
    if subject is None:
        subject = Subject(name=req.name, owner_id=user.id)
        db.add(subject)
        db.commit()
        db.refresh(subject)
    return SubjectOut(id=str(subject.id), name=subject.name)


# ---------------------------------------------------------------------------
# Question bank
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/questions", response_model=list[QuestionOut])
def list_subject_questions(
    subject_id: str,
    topic: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    bloom_level: str | None = Query(default=None),
    question_type: str | None = Query(default=None),
    verified: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Lists the questions of a subject's bank (with optional filters)."""
    subject = _get_owned_subject(db, subject_id, user)
    sid = subject.id

    # Filters supported by the service layer.
    verified_by_human: bool | None = None
    verified_by_ai: bool | None = None
    if verified == "human":
        verified_by_human = True
    elif verified == "ai":
        verified_by_ai = True
    elif verified == "none":
        verified_by_human = False

    questions = list_questions(
        subject_id=sid,
        verified_by_human=verified_by_human,
        verified_by_ai=verified_by_ai,
        topic=topic,
        limit=limit,
    )

    # In-memory filters (not supported by the service layer).
    if difficulty:
        questions = [q for q in questions if q.difficulty == difficulty]
    if bloom_level:
        questions = [q for q in questions if q.bloom_level == bloom_level]
    if question_type:
        questions = [q for q in questions if q.question_type == question_type]

    return [
        QuestionOut(
            id=str(q.id),
            subject_id=str(q.subject_id),
            topic=q.topic,
            subtopic=q.subtopic,
            bloom_level=q.bloom_level,
            difficulty=q.difficulty,
            question_type=q.question_type,
            question_text=q.question_text,
            options=q.options,
            expected_answer=q.expected_answer,
            solution_explanation=q.solution_explanation,
            points=q.points,
            verified_by_human=q.verified_by_human,
            verified_by_ai=q.verified_by_ai,
            ai_review_notes=q.ai_review_notes,
            ai_review_priority=q.ai_review_priority,
            source=q.source,
            created_at=str(q.created_at) if q.created_at else None,
        )
        for q in questions
    ]


# ---------------------------------------------------------------------------
# Bank export (download with LLM audit fields)
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/questions/export", response_model=BankExport)
def export_subject_bank(
    subject_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Downloads the subject's question bank with prompt/thinking audit fields.

    Correlation with `llm_responses`: the generator's raw answer is the item
    JSON persisted by the pipeline (`statement` -> `question_text`), so each
    question is matched by exact statement text against the most recent
    `pipeline:generator` row. `thinking` is only present when thinking
    capture was enabled at generation time (`llm_capture_thinking`).
    """
    subject = _get_owned_subject(db, subject_id, user)
    sid = subject.id

    questions = list_questions(subject_id=sid, limit=1000)

    # Statement -> (prompt, thinking, model) from generator audit rows,
    # newest first (the newest row wins on collision).
    audit_by_statement: dict[str, tuple[str | None, str | None, str | None]] = {}
    if questions:
        audit_rows = (
            db.query(LLMResponse)
            .filter(LLMResponse.context == "pipeline:generator")
            .order_by(LLMResponse.created_at.desc())
            .all()
        )
        for row in audit_rows:
            statement = _extract_statement(row.raw_answer)
            if statement and statement not in audit_by_statement:
                audit_by_statement[statement] = (row.prompt, row.raw_thinking, row.model)

    export_questions = [
        _build_export_question(q, *audit_by_statement.get(q.question_text or "", (None, None, None)))
        for q in questions
    ]

    return BankExport(
        subject=subject.name,
        exported_at=datetime.now(timezone.utc).isoformat(),
        total_questions=len(export_questions),
        thinking_capture_enabled=bool(settings.llm_capture_thinking),
        questions=export_questions,
    )


def _extract_statement(raw_answer: str | None) -> str | None:
    """Extracts the item `statement` from a generator raw answer JSON."""
    if not raw_answer or not raw_answer.strip().startswith("{"):
        return None
    try:
        data = json.loads(raw_answer)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        statement = data.get("statement")
        if isinstance(statement, str) and statement:
            return statement
    return None


@router.put("/questions/{question_id}/verification", response_model=QuestionOut)
def set_question_verification(
    question_id: str,
    req: VerificationRequest,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Marks/unmarks the human verification of a question (HITL).

    ⚠️ Felipe's requirement: a human-verified question is immutable for the
    agent (guardrail in `core.domain.bank.service`).
    """
    try:
        qid = uuid.UUID(question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id")

    q = db.get(GeneratedQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="Question not found")
    _get_owned_subject(db, str(q.subject_id), user)

    q = set_human_verification(qid, verified=req.verified)
    return QuestionOut(
        id=str(q.id),
        subject_id=str(q.subject_id),
        topic=q.topic,
        subtopic=q.subtopic,
        bloom_level=q.bloom_level,
        difficulty=q.difficulty,
        question_type=q.question_type,
        question_text=q.question_text,
        options=q.options,
        expected_answer=q.expected_answer,
        solution_explanation=q.solution_explanation,
        points=q.points,
        verified_by_human=q.verified_by_human,
        verified_by_ai=q.verified_by_ai,
        ai_review_notes=q.ai_review_notes,
        ai_review_priority=q.ai_review_priority,
        source=q.source,
        created_at=str(q.created_at) if q.created_at else None,
    )


@router.delete("/questions/{question_id}")
def delete_question_endpoint(
    question_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Deletes a question (blocked if verified by a human)."""
    try:
        qid = uuid.UUID(question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id")

    q = db.get(GeneratedQuestion, qid)
    if q is None:
        raise HTTPException(status_code=404, detail="Question not found")
    _get_owned_subject(db, str(q.subject_id), user)

    try:
        delete_question(qid)
    except SecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"deleted": True, "id": question_id}
