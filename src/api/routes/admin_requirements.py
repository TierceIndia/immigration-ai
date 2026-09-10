"""Admin requirement review routes — list pending review and approve/reject via HTTP.

All mutations require admin role. Reuses existing requirement_repository.py logic
for semantic-key dedup (NEW/EXACT_DUPLICATE/COMPLEMENTARY/CONFLICT).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.models import Requirement, RequirementReview
from db.repositories import RequirementRepo
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


class RequirementReviewRequest(BaseModel):
    requirement_id: str = Field(..., description="Requirement ID")
    reviewer: str = Field(..., description="User ID or name of the reviewer")
    action: str = Field(..., description="'APPROVE', 'REJECT', or 'MARK_NEEDS_REVIEW'")
    notes: str | None = None


class RequirementSummary(BaseModel):
    requirement_id: str
    category: str
    name: str
    field: str | None = None
    operator: str | None = None
    value: Any = None
    unit: str | None = None
    mandatory: bool = False
    status: str
    current_version: int = 1
    extracted_at: str | None = None


def _record_review(session: Session, requirement_id: str, reviewer: str,
                   action: str, notes: str | None = None) -> None:
    """Record a requirement review using ORM (portable across SQLite + PostgreSQL)."""
    review = RequirementReview(
        requirement_id=requirement_id,
        reviewer=reviewer,
        action=action,
        notes=notes,
    )
    session.add(review)


def _req_to_summary(req: Requirement) -> dict:
    return {
        "requirement_id": req.requirement_id,
        "category": req.category,
        "name": req.name,
        "field": req.field,
        "operator": req.operator,
        "value": json.loads(req.value_json) if req.value_json else None,
        "unit": req.unit,
        "mandatory": bool(req.mandatory),
        "status": req.status,
        "current_version": req.current_version or 1,
        "extracted_at": None,
    }


@router.get("/review-queue", response_model=dict[str, Any])
def review_queue(
    request: Request,
    status: str = "NEEDS_REVIEW",
    program_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List requirements awaiting human review."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        stmt = select(Requirement).filter(Requirement.status == status)
        if program_id:
            stmt = stmt.where(Requirement.program_id == program_id)
        reqs = list(session.execute(stmt).scalars())
        result = [_req_to_summary(r) for r in reqs]
        return {"requirements": result, "status": status, "program_id": program_id, "count": len(result)}


@router.post("/review/{requirement_id}/approve", response_model=RequirementSummary)
def approve_requirement(
    requirement_id: str,
    body: RequirementReviewRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Approve a requirement (change status from NEEDS_REVIEW to APPROVED)."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = RequirementRepo(session)
        req = repo.get(requirement_id)
        if not req:
            raise HTTPException(status_code=404, detail=f"Requirement {requirement_id} not found")
        req.status = "APPROVED"
        req.updated_at = datetime.now(timezone.utc)
        _record_review(session, requirement_id, body.reviewer, "APPROVE", body.notes)
        session.commit()
        return _req_to_summary(req)


@router.post("/review/{requirement_id}/reject", response_model=RequirementSummary)
def reject_requirement(
    requirement_id: str,
    body: RequirementReviewRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Reject a requirement (change status to REJECTED)."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = RequirementRepo(session)
        req = repo.get(requirement_id)
        if not req:
            raise HTTPException(status_code=404, detail=f"Requirement {requirement_id} not found")
        req.status = "REJECTED"
        req.updated_at = datetime.now(timezone.utc)
        _record_review(session, requirement_id, body.reviewer, "REJECT", body.notes)
        session.commit()
        return _req_to_summary(req)


@router.post("/review/{requirement_id}/mark-needs-review", response_model=RequirementSummary)
def mark_needs_review(
    requirement_id: str,
    body: RequirementReviewRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Mark a requirement as needing human review."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = RequirementRepo(session)
        req = repo.get(requirement_id)
        if not req:
            raise HTTPException(status_code=404, detail=f"Requirement {requirement_id} not found")
        req.status = "NEEDS_REVIEW"
        req.updated_at = datetime.now(timezone.utc)
        _record_review(session, requirement_id, body.reviewer, "MARK_NEEDS_REVIEW", body.notes)
        session.commit()
        return _req_to_summary(req)


@router.get("/{requirement_id}", response_model=RequirementSummary)
def get_requirement(
    requirement_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get a single requirement by ID."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = RequirementRepo(session)
        req = repo.get(requirement_id)
        if not req:
            raise HTTPException(status_code=404, detail=f"Requirement {requirement_id} not found")
        value = json.loads(req.value_json) if req.value_json else None
        return {
            "requirement_id": req.requirement_id,
            "category": req.category,
            "name": req.name,
            "field": req.field,
            "operator": req.operator,
            "value": value,
            "unit": req.unit,
            "mandatory": bool(req.mandatory),
            "status": req.status,
            "current_version": req.current_version or 1,
            "extracted_at": None,
        }