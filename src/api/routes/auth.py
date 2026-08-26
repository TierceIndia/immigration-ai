"""Auth routes — register, login, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from api.schemas.models import RegisterResponse, TokenResponse, UserCreate, UserResponse
from db.connection import get_session, get_db_engine
from logging_config import get_logger
from services.auth_service import AuthService, decode_access_token

router = APIRouter()
logger = get_logger(__name__)


def get_current_user(request: Request) -> dict:
    """Extract and validate JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AuthService(session)
        user = svc.get_current_user(token)
        if not user:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return user


def get_current_user_id(request: Request) -> str:
    """Extract user_id from JWT — used by applicant/assessment routes."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header[7:]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return user_id


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(body: UserCreate, request: Request):
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AuthService(session)
        try:
            result = svc.register(body.email, body.password, body.full_name)
        except IntegrityError:
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
        )


@router.get("/me", response_model=UserResponse)
def get_me(request: Request, current_user: dict = Depends(get_current_user)):
    return UserResponse(
        user_id=current_user["user_id"],
        email=current_user["email"],
        full_name=current_user.get("full_name"),
        role=current_user["role"],
    )
