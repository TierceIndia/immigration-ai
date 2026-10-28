"""Pipeline run routes — query sync execution history.

Admin role required. Provides audit trail for government source sync
operations. Only GET /{run_id} is exposed (status polling for Run
now/Run all) - the frontend has no pipeline-runs list/summary/per-source
history page, so those routes were removed (see DESIGN.md).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from api.helpers import parse_uuid_or_404
from api.routes.auth import require_admin
from db.connection import get_session, get_db_engine
from db.models import Rule
from db.repositories import PipelineRunRepo
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _compute_rule_counts(session, source_id: str) -> dict[str, int]:
    """Count rules by status for the given source."""
    stmt = (
        select(func.count(), Rule.status)
        .where(Rule.source_id == source_id)
        .group_by(Rule.status)
    )
    rows = list(session.execute(stmt).all())
    counts = {row[1]: row[0] for row in rows}
    return {
        "rules_validated": counts.get("VALIDATED", 0),
        "rules_approved": counts.get("APPROVED", 0),
        "rules_activated": counts.get("ACTIVE", 0),
    }


def _run_to_dict(run, session=None) -> dict[str, Any]:
    base = {
        "run_id": run.run_id,
        "source_id": run.source_id,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "old_hash": run.old_hash,
        "new_hash": run.new_hash,
        # requirements_extracted counts attempts, not stored rows (item 6) -
        # stored_new is the real "new knowledge" figure; the UI should show
        # that, not requirements_extracted, as the headline count.
        "requirements_extracted": run.requirements_extracted,
        "stored_new": run.stored_new,
        "requirements_approved": run.requirements_approved,
        "rules_created": run.rules_created,
        "errors": run.errors or [],
        "metadata": run.metadata_json or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if session and run.source_id:
        counts = _compute_rule_counts(session, run.source_id)
        base.update(counts)
    else:
        base["rules_validated"] = run.rules_validated
        base["rules_approved"] = run.rules_approved
        base["rules_activated"] = run.rules_activated
    return base


@router.get("/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Get a single pipeline run by ID, or a background sync job's progress.

    A background sync started via POST /api/v1/admin/sync has its own run_id
    distinct from any per-source PipelineRun (sync_all_sources creates one
    PipelineRun per source, not one for the whole batch) — checked first here
    since it's a cheap in-memory lookup, falling back to the PipelineRun table.
    """
    parse_uuid_or_404(run_id)

    from scheduler import get_sync_job
    job = get_sync_job(run_id)
    if job is not None:
        return job

    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = PipelineRunRepo(session)
        run = repo.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Pipeline run not found")
        return _run_to_dict(run, session)
