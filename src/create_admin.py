"""Admin bootstrap — create a new admin user or promote an existing one.

Idempotent. Safe to run repeatedly.

Usage (against the DATABASE_URL in the environment):
    python src/create_admin.py --email admin@example.com --password 'S3cure!' [--full-name "Admin"]
    python src/create_admin.py --email admin@example.com          # promote existing user only
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='S3cure!' python src/create_admin.py

Behavior:
  * User already exists  -> role upgraded to "admin" (password untouched if not given).
  * User does not exist  -> created with role "admin" (password required).
"""
from __future__ import annotations

import argparse
import os

from db.connection import get_database_url, get_engine, get_session
from db.repositories import UserRepo
from logging_config import get_logger
from services.auth_service import AuthService, hash_password

logger = get_logger(__name__)

_ADMIN_EMAIL_ENV = "ADMIN_EMAIL"
_ADMIN_PASSWORD_ENV = "ADMIN_PASSWORD"
_ADMIN_FULL_NAME_ENV = "ADMIN_FULL_NAME"


def _require_credentials(email: str, password: str | None) -> None:
    if not email:
        raise ValueError(
            f"{_ADMIN_EMAIL_ENV} must be set to bootstrap an admin user"
        )
    if not password:
        raise ValueError(
            f"{_ADMIN_PASSWORD_ENV} must be set to bootstrap an admin user "
            "(a default password is never assumed)"
        )


def bootstrap_admin_from_env(engine) -> dict | None:
    """Idempotently create or promote an admin user from environment variables.

    Reads ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_FULL_NAME. Only when BOTH
    ADMIN_EMAIL and ADMIN_PASSWORD are set does it act (never assumes a default
    admin or password). If either is missing, no user is created and None is
    returned so callers can treat admin bootstrap as optional.

    Returns {"action": "created"|"promoted", ...} or None when env is absent.
    """
    email = os.getenv(_ADMIN_EMAIL_ENV)
    password = os.getenv(_ADMIN_PASSWORD_ENV)
    if not email or not password:
        return None
    _require_credentials(email, password)
    full_name = os.getenv(_ADMIN_FULL_NAME_ENV)
    return promote_or_create(engine, email, password, full_name)


def promote_or_create(engine, email: str, password: str | None, full_name: str | None) -> dict:
    with get_session(engine) as session:
        repo = UserRepo(session)
        user = repo.get_by_email(email)
        if user is not None:
            repo.promote_to_admin(str(user.user_id))
            session.commit()
            logger.info("Promoted user to admin", extra={"email": email, "user_id": str(user.user_id)})
            return {"user_id": str(user.user_id), "email": email, "action": "promoted"}
        if not password:
            raise ValueError(f"User {email!r} does not exist — a password is required to create it")
        hashed = hash_password(password)
        user = repo.create(email=email, password_hash=hashed,
                           full_name=full_name, role="admin")
        session.commit()
        logger.info("Created admin user", extra={"email": email, "user_id": str(user.user_id)})
        return {"user_id": str(user.user_id), "email": email, "action": "created"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an admin user idempotently.")
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL"), help="Admin email")
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"),
                        help="Password (required only when creating a brand-new user)")
    parser.add_argument("--full-name", default=os.getenv("ADMIN_FULL_NAME"), help="Display name")
    args = parser.parse_args()

    if not args.email:
        parser.error("--email (or ADMIN_EMAIL) is required")

    engine = get_engine(get_database_url())
    result = promote_or_create(engine, args.email, args.password, args.full_name)
    print(f"{result['action'].upper()}: {result['email']} (user_id={result['user_id']}, role=admin)")


if __name__ == "__main__":
    main()