"""Auth endpoints: register, login and current user.

- POST /auth/register : creates a professor account (returns JWT).
- POST /auth/login    : email + password → JWT.
- GET  /auth/me       : current authenticated user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from core.domain.auth import (
    create_access_token,
    get_current_user,
    get_db,
    hash_password,
    verify_password,
)
from core.domain.models import User

from exam_app.schemas.bank import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, name=user.name, role=user.role)


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: OrmSession = Depends(get_db)):
    """Creates a professor account and returns a JWT session."""
    email = req.email.strip().lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=email,
        name=req.name.strip(),
        password_hash=hash_password(req.password),
        role="professor",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user), user=_to_user_out(user))


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: OrmSession = Depends(get_db)):
    """Email + password → JWT session."""
    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user), user=_to_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """Current authenticated user."""
    return _to_user_out(user)
