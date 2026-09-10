"""Authentication service — password hashing, JWT tokens, user management."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from db.repositories import ApplicantRepo
from logging_config import get_logger

logger = get_logger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production":
            raise ValueError(
                "JWT_SECRET_KEY environment variable must be set in production"
            )
        logger.warning(
            "JWT_SECRET_KEY not set — using development default (not for production use)"
        )
        return "dev-secret-change-me"
    return secret


def _get_jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _get_jwt_expiration() -> int:
    return int(os.getenv("JWT_EXPIRATION_MINUTES", "1440"))


def create_access_token(user_id: str, role: str = "applicant") -> dict:
    """Create JWT access token. Returns dict with token + metadata."""
    secret = _get_jwt_secret()
    algorithm = _get_jwt_algorithm()
    exp_minutes = _get_jwt_expiration()

    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=exp_minutes)

    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": exp,
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": exp_minutes * 60,
    }


def decode_access_token(token: str) -> dict | None:
    """Decode and validate JWT. Returns payload or None if invalid."""
    secret = _get_jwt_secret()
    algorithm = _get_jwt_algorithm()
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        return payload
    except JWTError as e:
        logger.warning("Invalid JWT token", extra={"error": str(e)})
        return None


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class AuthService:
    def __init__(self, session):
        from db.repositories import UserRepo
        self._user_repo = UserRepo(session)

    def email_registered(self, email: str) -> bool:
        """True if an account with this email already exists."""
        return self._user_repo.get_by_email(email) is not None

    def register(self, email: str, password: str, full_name: str | None = None) -> dict:
        hashed = hash_password(password)
        user = self._user_repo.create(email=email, password_hash=hashed, full_name=full_name)
        token_data = create_access_token(str(user.user_id), role=user.role)
        logger.info("User registered", extra={"user_id": str(user.user_id)})
        return {
            "user_id": str(user.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            **token_data,
        }

    def login(self, email: str, password: str) -> dict | None:
        user = self._user_repo.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            logger.warning("Login failed", extra={"email": email})
            return None
        token_data = create_access_token(str(user.user_id), role=user.role)
        logger.info("User logged in", extra={"user_id": str(user.user_id)})
        return {
            "user_id": str(user.user_id),
            "email": user.email,
            "role": user.role,
            **token_data,
        }

    def get_current_user(self, token: str) -> dict | None:
        payload = decode_access_token(token)
        if not payload:
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        # G17: the `sub` claim is attacker-influenceable in shape. The underlying
        # api_users.user_id is a native UUID column on PostgreSQL; passing a malformed
        # string to UserRepo.get() would raise a raw DataError/InvalidTextRepresentation
        # (unhandled 500) instead of the correct 401. Validate the UUID format at the
        # edge first, mirroring parse_uuid_or_404, but return None (this function's
        # contract) rather than raising HTTPException.
        try:
            UUID(user_id)
        except (ValueError, TypeError):
            logger.warning("JWT sub claim is not a valid UUID", extra={"user_id": user_id})
            return None
        user = self._user_repo.get(user_id)
        if not user or not user.is_active:
            return None
        return {
            "user_id": str(user.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        }
