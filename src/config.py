"""Central configuration and environment loading — single source of truth.

Every entry point (FastAPI, Alembic, seed_registry, create_admin, government
sync, scheduler) should resolve the database and other runtime configuration
through this module so the whole application behaves consistently.

It loads the project's ``.env`` file once (via python-dotenv). ``load_dotenv``
never overrides variables already present in the real process environment, so an
operator can always override ``.env`` values with actual environment variables.
"""
from __future__ import annotations

import os

import dotenv

# Load once, at import. Idempotent; does not override existing env vars.
dotenv.load_dotenv()

# Convenience alias so the rest of the codebase can do `from config import getenv`.
getenv = os.getenv


def get_database_url() -> str:
    """Return DATABASE_URL, raising a clear error if it is not configured."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Copy .env.example to .env and configure it, "
            "or export DATABASE_URL in the environment."
        )
    return url


def database_query() -> str:
    """Return 'postgresql' | 'sqlite' | 'unknown' for the configured DATABASE_URL.

    Returns 'sqlite' when DATABASE_URL is absent (the configurable backend
    defaults to SQLite for legacy/test workflows).
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        return "sqlite"
    if url.startswith("postgres"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    return "unknown"


def scrub_credential_string(text: str | None) -> str | None:
    """Redact credentials that may appear inside a diagnostic/error string.

    Handles the common ``scheme://user:password@host`` shape produced by
    SQLAlchemy/psycopg when it echoes a malformed connection string back in an
    error, e.g. ``postgresql://immigration:immigration_dev@localhost`` becomes
    ``postgresql://immigration:***@localhost``. Never logs real passwords.
    """
    if not text:
        return text
    import re
    # scheme://user:password@host  -> keep user, redact password
    scrubbed = re.sub(
        r"(://[^:/@\s]+):([^@\s]+)@",
        r"\1:***@",
        text,
    )
    # Also catch a bare `password=PASS` / `PASSWORD=PASS` query/param spelling.
    scrubbed = re.sub(
        r"(?i)(password\s*[=:]\s*)[^\s;&,]+",
        r"\1***",
        scrubbed,
    )
    return scrubbed


def database_info() -> dict:
    """Safe, diagnostic description of the configured database.

    Never includes the password. Returns:
      {"configured": bool, "backend": str, "database": str|None, "host": str|None}
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        return {
            "configured": False,
            "backend": "sqlite",
            "database": None,
            "host": None,
        }
    from sqlalchemy.engine import make_url
    try:
        parsed = make_url(url)
        return {
            "configured": True,
            "backend": parsed.get_backend_name(),
            "database": parsed.database,
            "host": parsed.host,
        }
    except Exception:
        return {
            "configured": True,
            "backend": database_query(),
            "database": None,
            "host": None,
        }
