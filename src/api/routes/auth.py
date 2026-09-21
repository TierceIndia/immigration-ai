"""Auth routes — register, login, me."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from api.schemas.models import RegisterResponse, TokenResponse, UserCreate, UserResponse
from db.connection import get_session, get_db_engine
from logging_config import get_logger
from services.auth_service import AuthService, decode_access_token

router = APIRouter()
logger = get_logger(__name__)


# ============================================================================
# AUTH TEMPORARILY DISABLED — 2026-09-21, at user's explicit request, to test
# business logic without login friction. Every request is treated as the
# bootstrap admin user (ADMIN_EMAIL), regardless of Authorization header.
# require_admin / get_current_user_id / every route's `_require_admin(...)`
# all compose on this function, so this one bypass covers the whole API.
#
# TO RESTORE REAL AUTH: delete this function and uncomment the original
# implementation below it.
# ============================================================================
def get_current_user(request: Request) -> dict:
    """BYPASSED: always returns the real bootstrap admin DB row (not a token check)."""
    engine = get_db_engine(request)
    with get_session(engine) as session:
        from db.repositories import UserRepo
        user_repo = UserRepo(session)
        admin_email = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
        user = user_repo.get_by_email(admin_email)
        if not user:
            raise HTTPException(
                status_code=401,
                detail=(
                    "AUTH DISABLED bypass has no bootstrap admin user to impersonate. "
                    "Set ADMIN_EMAIL/ADMIN_PASSWORD and restart, or re-enable real auth "
                    "in src/api/routes/auth.py."
                ),
            )
        return {
            "user_id": str(user.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        }


# ---------------------------------------------------------------------------
# ORIGINAL IMPLEMENTATION (commented out while auth is disabled — uncomment
# and delete the bypass above to restore real JWT authentication):
#
# def get_current_user(request: Request) -> dict:
#     """Extract and validate JWT from Authorization header."""
#     auth_header = request.headers.get("Authorization", "")
#     if not auth_header.startswith("Bearer "):
#         raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
#     token = auth_header[7:]
#     payload = decode_access_token(token)
#     if not payload:
#         raise HTTPException(status_code=401, detail="Invalid or expired token")
#     user_id = payload.get("sub")
#     if not user_id:
#         raise HTTPException(status_code=401, detail="Invalid token payload")
#     engine = get_db_engine(request)
#     with get_session(engine) as session:
#         svc = AuthService(session)
#         user = svc.get_current_user(token)
#         if not user:
#             raise HTTPException(status_code=401, detail="User not found or inactive")
#         return user
# ---------------------------------------------------------------------------


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Reusable dependency for admin-only endpoints.

    Composes on top of get_current_user (which enforces a valid Bearer JWT and an
    active user) and then rejects non-admin roles with 403. Use as:
    ``current_user: dict = Depends(require_admin)``.
    """
    role = (current_user or {}).get("role")
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return current_user


def get_current_user_id(request: Request) -> str:
    """Return the active authenticated user's ID for user-owned routes."""
    return get_current_user(request)["user_id"]


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(body: UserCreate, request: Request):
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AuthService(session)
        # Deterministic duplicate rejection: check first so a re-registration attempt
        # (including one targeting an existing admin account with role="applicant")
        # is answered with a clean 409 and never even reaches the DB constraint.
        if svc.email_registered(body.email):
            raise HTTPException(status_code=409, detail="Email already registered")
        try:
            result = svc.register(body.email, body.password, body.full_name)
        except IntegrityError:
            # Race backstop (two concurrent requests) — keep the same clean 409.
            raise HTTPException(status_code=409, detail="Email already registered")
        return RegisterResponse(
            user_id=result["user_id"],
            email=result["email"],
            full_name=result["full_name"],
            role=result["role"],
            access_token=result["access_token"],
            token_type=result["token_type"],
            expires_in=result["expires_in"],
        )


@router.post("/login", response_model=TokenResponse)
def login(body: UserCreate, request: Request):
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AuthService(session)
        result = svc.login(body.email, body.password)
        if not result:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return TokenResponse(
            access_token=result["access_token"],
            token_type=result["token_type"],
            expires_in=result["expires_in"],
            role=result["role"],
        )


@router.get("/me", response_model=UserResponse)
def get_me(request: Request, current_user: dict = Depends(get_current_user)):
    return UserResponse(
        user_id=current_user["user_id"],
        email=current_user["email"],
        full_name=current_user.get("full_name"),
        role=current_user["role"],
    )
