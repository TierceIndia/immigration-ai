"""Pipeline run routes — query sync execution history.

All endpoints require admin role. Provides audit trail for government
source sync operations.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.repositories import PipelineRunRepo
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def _run_to_dict(run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "source_id": run.source_id,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "old_hash": run.old_hash,
        "new_hash": run.new_hash,
        "requirements_extracted": run.requirements_extracted,
        "requirements_approved": run.requirements_approved,
        "rules_created": run.rules_created,
        "rules_validated": run.rules_validated,
        "rules_approved": run.rules_approved,
        "rules_activated": run.rules_activated,
        "errors": run.errors or [],
        "metadata": run.metadata_json or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("")
def list_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """List all pipeline runs, newest first."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = PipelineRunRepo(session)
        runs = repo.list_all(limit=limit, offset=offset)
        return {
            "runs": [_run_to_dict(r) for r in runs],
            "total": len(runs),
            "limit": limit,
            "offset": offset,
        }


@router.get("/summary")
def run_summary(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get aggregate counts by status."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = PipelineRunRepo(session)
        return {"counts": repo.count_by_status()}


@router.get("/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get a single pipeline run by ID."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = PipelineRunRepo(session)
        run = repo.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        return _run_to_dict(run)


@router.get("/source/{source_id}")
def runs_for_source(
    source_id: str,
    request: Request,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
):
    """List pipeline runs for a specific source."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = PipelineRunRepo(session)
        runs = repo.list_for_source(source_id, limit=limit)
        return {
            "source_id": source_id,
            "runs": [_run_to_dict(r) for r in runs],
            "total": len(runs),
        }
