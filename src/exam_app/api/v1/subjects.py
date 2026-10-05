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

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from core.domain.models import Subject, GeneratedQuestion
from core.domain.bank.service import (
    SecurityError,
    list_questions,
    set_human_verification,
    delete_question,
)
from core.infrastructure.db.connection import SessionLocal

from exam_app.schemas.bank import (
    QuestionFilters,
    QuestionOut,
    SubjectCreate,
    SubjectOut,
    VerificationRequest,
)

router = APIRouter(tags=["subjects"])


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects():
    """Lists registered subjects."""
    db = SessionLocal()
    try:
        subjects = db.query(Subject).order_by(Subject.name).all()
        return [
            SubjectOut(id=str(s.id), name=s.name)
            for s in subjects
        ]
    finally:
        db.close()


@router.post("/subjects", response_model=SubjectOut)
def create_subject(req: SubjectCreate):
    """Registers a subject (or returns the existing one, without duplicating)."""
    db = SessionLocal()
    try:
        subject = (
            db.query(Subject).filter(Subject.name.ilike(req.name)).first()
        )
        if subject is None:
            subject = Subject(name=req.name)
            db.add(subject)
            db.commit()
            db.refresh(subject)
        return SubjectOut(id=str(subject.id), name=subject.name)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Banco de preguntas
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
):
    """Lists the questions of a subject's bank (with optional filters)."""
    # Validar UUID
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")

    db = SessionLocal()
    try:
        if db.get(Subject, sid) is None:
            raise HTTPException(status_code=404, detail="Subject not found")
    finally:
        db.close()

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


@router.put("/questions/{question_id}/verification", response_model=QuestionOut)
def set_question_verification(question_id: str, req: VerificationRequest):
    """Marks/unmarks the human verification of a question (HITL).

    ⚠️ Felipe's requirement: a human-verified question is immutable for the
    agent (guardrail in `core.domain.bank.service`).
    """
    try:
        qid = uuid.UUID(question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id")

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
def delete_question_endpoint(question_id: str):
    """Deletes a question (blocked if verified by a human)."""
    try:
        qid = uuid.UUID(question_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid question_id")
    try:
        delete_question(qid)
    except SecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"deleted": True, "id": question_id}
