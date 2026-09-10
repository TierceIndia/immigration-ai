"""Health check routes — liveness + readiness.

GET /health  → process is alive (always 200)
GET /ready   → DB available + tables exist (200 or 503)

When the DB is disconnected, /ready includes safe, non-secret diagnostics
(configured backend/host/name and the scrubbed last connection error) so an
operator can diagnose a broken setup without grepping logs. Credentials are
scrubbed before they can reach the response body.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — is the process running? Always returns 200."""
    return {"status": "ok"}


def _diagnostics_for_disconnect(request: Request) -> dict:
    """Safe, non-secret context describing a failed DB initialization."""
    try:
        from config import database_info, scrub_credential_string
    except Exception:  # pragma: no cover - config import should never fail
        return {}
    info = database_info()
    body: dict = {
        "configured_backend": info.get("backend"),
        "configured_host": info.get("host"),
        "configured_database": info.get("database"),
    }
    if not info.get("configured"):
        body["configured"] = False
    # The last connection error is message-only and credential-scrubbed. It is
    # surfaced only outside TESTING: CI/harness environments should not depend on
    # a live DB error detail and it keeps the response stable for tests.
    if os.getenv("TESTING") != "true":
        raw_error = getattr(request.app.state, "db_error", None)
        if raw_error:
            body["db_error_detail"] = scrub_credential_string(str(raw_error))
    return body


@router.get("/ready")
async def readiness_check(request: Request):
    """Readiness probe — can the application serve traffic?

    Returns 200 if DB is connected and tables exist.
    Returns 503 if DB is not available.
    """
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "db": "disconnected",
                **_diagnostics_for_disconnect(request),
            },
        )

    # Verify DB is actually reachable (not just configured)
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unreachable"},
        )

    return {"status": "ok", "db": "connected"}
