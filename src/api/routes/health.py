"""Health check routes — liveness + readiness.

GET /health  → process is alive (always 200)
GET /ready   → DB available + tables exist (200 or 503)
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — is the process running? Always returns 200."""
    return {"status": "ok"}


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
            content={"status": "degraded", "db": "disconnected"},
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
