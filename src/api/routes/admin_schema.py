"""Admin schema-report route — read-only database structure.

GET /api/v1/admin/schema-report → tables, columns, primary/foreign keys,
exact row counts, current alembic version, dialect. Only introspection
queries run; nothing is written.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.routes.auth import get_current_user
from db.connection import get_db_engine
from db.schema_info import tables_snapshot
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("")
def schema_report(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Full database structure report (admin only, read-only)."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    try:
        return tables_snapshot(engine)
    except Exception as exc:
        logger.error("Schema report failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Could not read database schema")