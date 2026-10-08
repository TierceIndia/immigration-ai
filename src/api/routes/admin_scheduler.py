"""Admin scheduler settings — view and change the global government sync
schedule (daily cron time, or an interval). The schedule is global, not
per-source. All mutations require admin role.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.repositories import SchedulerSettingsRepo
from logging_config import get_logger
from scheduler import apply_live_schedule, compute_next_run_at

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class SchedulerResponse(BaseModel):
    enabled: bool
    mode: str
    hour_utc: int | None = None
    minute_utc: int | None = None
    interval_minutes: int | None = None
    next_run_at: str | None = None


class SchedulerUpdateRequest(BaseModel):
    enabled: bool
    mode: str = Field(..., description="'daily' or 'interval'")
    hour_utc: int | None = Field(None, ge=0, le=23)
    minute_utc: int | None = Field(None, ge=0, le=59)
    interval_minutes: int | None = Field(None, ge=15)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in ("daily", "interval"):
            raise ValueError("mode must be 'daily' or 'interval'")
        return v


def _settings_to_response(obj) -> SchedulerResponse:
    next_run_at = None
    if obj.enabled:
        next_run_at = compute_next_run_at(obj.interval_minutes, obj.hour_utc, obj.minute_utc)
    return SchedulerResponse(
        enabled=obj.enabled,
        mode=obj.mode,
        hour_utc=obj.hour_utc,
        minute_utc=obj.minute_utc,
        interval_minutes=obj.interval_minutes,
        next_run_at=next_run_at,
    )


@router.get("", response_model=SchedulerResponse)
def get_scheduler_settings(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Current global sync schedule and its next computed run time."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        settings = SchedulerSettingsRepo(session).get()
        session.commit()
        return _settings_to_response(settings)


@router.put("", response_model=SchedulerResponse)
def update_scheduler_settings(
    body: SchedulerUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Change the global sync schedule. Persists to scheduler_settings (so it
    survives a restart) and applies it to the live scheduler job immediately,
    best-effort, via APScheduler's reschedule_job/pause_job/resume_job."""
    _require_admin(current_user)
    if body.mode == "daily" and (body.hour_utc is None or body.minute_utc is None):
        raise HTTPException(
            status_code=422,
            detail="hour_utc and minute_utc are required when mode='daily'",
        )
    if body.mode == "interval" and body.interval_minutes is None:
        raise HTTPException(
            status_code=422,
            detail="interval_minutes is required when mode='interval'",
        )

    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = SchedulerSettingsRepo(session)
        updated = repo.update(
            enabled=body.enabled,
            mode=body.mode,
            hour_utc=body.hour_utc if body.mode == "daily" else None,
            minute_utc=body.minute_utc if body.mode == "daily" else None,
            interval_minutes=body.interval_minutes if body.mode == "interval" else None,
            updated_by=current_user.get("email"),
        )
        session.commit()
        apply_live_schedule(
            updated.interval_minutes, updated.hour_utc, updated.minute_utc, updated.enabled,
        )
        logger.info(
            "Scheduler settings updated",
            extra={"mode": updated.mode, "enabled": updated.enabled,
                   "updated_by": updated.updated_by},
        )
        return _settings_to_response(updated)
