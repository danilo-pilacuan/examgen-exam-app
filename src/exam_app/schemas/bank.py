"""DTOs for subjects, question bank, exams and material.

Mirror of the frontend types (`projects/frontend/src/types/domain.ts`).
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
class SubjectOut(BaseModel):
    """Registered subject (GET /subjects)."""

    id: str
    name: str


class SubjectCreate(BaseModel):
    """Subject registration (POST /subjects)."""

    name: str = Field(..., min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Question bank
# ---------------------------------------------------------------------------
class QuestionOut(BaseModel):
    """Question from the bank (row of generated_questions)."""

    id: str
    subject_id: str
    topic: str
    subtopic: str | None = None
    bloom_level: str
    difficulty: str
    question_type: str | None = None
    question_text: str
    options: list[dict[str, Any]] | None = None
    expected_answer: str | None = None
    solution_explanation: str | None = None
    points: float | None = None
    verified_by_human: bool = False
    verified_by_ai: bool = False
    ai_review_notes: str | None = None
    ai_review_priority: str | None = None
    source: str | None = None
    created_at: str | None = None


class QuestionFilters(BaseModel):
    """Filters for bank listing (query params of GET /subjects/{id}/questions)."""

    topic: str | None = None
    difficulty: str | None = None
    bloom_level: str | None = None
    question_type: str | None = None
    verified: str | None = None  # all | human | ai | none
    limit: int = Field(default=200, ge=1, le=1000)


class VerificationRequest(BaseModel):
    """Body of PUT /questions/{id}/verification."""

    verified: bool


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
class ExamOut(BaseModel):
    """Generated exam (GET /subjects/{id}/exams)."""

    id: str
    subject_id: str
    subject_name: str
    blueprint: dict[str, Any]
    items: list[dict[str, Any]]
    pdf_paths: list[str]
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------
class MaterialChunkOut(BaseModel):
    """Indexed material chunk (GET /subjects/{id}/material)."""

    id: str
    source_file: str
    chunk_index: int
    content: str


class MaterialDeleteRequest(BaseModel):
    """Body of DELETE /subjects/{id}/material."""

    source_file: str | None = None
    delete_all: bool = False


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    """Authenticated user (GET /auth/me)."""

    id: str
    email: str
    name: str
    role: str


class RegisterRequest(BaseModel):
    """Body of POST /auth/register."""

    email: str = Field(..., min_length=3, max_length=255)
    name: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)


class LoginRequest(BaseModel):
    """Body of POST /auth/login."""

    email: str
    password: str


class TokenResponse(BaseModel):
    """Response of POST /auth/login and POST /auth/register."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def to_uuid(value: str) -> uuid.UUID:
    """Converts a string to a UUID (raises ValueError if invalid)."""
    return uuid.UUID(value)
