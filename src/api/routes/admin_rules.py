"""Admin rule review routes — approve, reject, validate, activate rules.

All mutations require admin role. The human approval flow:
    DRAFT → VALIDATED (automated or admin) → APPROVED (admin) → ACTIVE (admin)
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.routes.auth import get_current_user
from db.connection import get_session, get_db_engine
from db.models import Rule as RuleORM, RuleVersion
from logging_config import get_logger
from rules.rule_repository import EnhancedRuleRepo
from rules.rule_activation import can_transition

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class RuleActionRequest(BaseModel):
    actor: str | None = Field(
        None,
        description="Deprecated and ignored - the audit trail always records the "
                     "authenticated admin, never a client-supplied value. Kept "
                     "optional only for backward compatibility with existing callers.",
    )
    notes: str | None = None


class RuleResponse(BaseModel):
    rule_id: str
    requirement_id: str | None = None
    program_id: str | None = None
    category: str | None = None
    field: str | None = None
    operator: str | None = None
    value: Any = None
    unit: str | None = None
    mandatory: bool = False
    critical: bool = False
    message: str | None = None
    status: str
    current_version: int = 1
    evidence_text: str | None = None
    created_at: str | None = None


class ReviewQueueResponse(BaseModel):
    drafts: list[dict]
    validated: list[dict]
    approved_pending: list[dict]
    active: list[dict]
    counts: dict


class BatchActionRequest(BaseModel):
    rule_ids: list[str]
    actor: str | None = Field(
        None,
        description="Deprecated and ignored - see RuleActionRequest.actor.",
    )
    notes: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


def _actor_from(current_user: dict) -> str:
    """The audit-trail actor identity for a rule action: ALWAYS the
    authenticated admin, never the request body's own `actor` field (which
    is accepted for backward compatibility but ignored - a client could
    previously type any name there and have it recorded as who acted,
    which defeats the point of an audit trail)."""
    return current_user.get("email") or current_user.get("user_id") or "unknown-admin"


def _orm_to_response(obj: RuleORM) -> RuleResponse:
    return RuleResponse(
        rule_id=obj.rule_id,
        requirement_id=obj.requirement_id,
        program_id=obj.program_id,
        category=obj.category,
        field=obj.field,
        operator=obj.operator,
        value=json.loads(obj.value_json) if obj.value_json else None,
        unit=obj.unit,
        mandatory=obj.mandatory or False,
        critical=obj.critical or False,
        message=obj.message,
        status=obj.status,
        current_version=obj.current_version or 1,
        evidence_text=obj.evidence_text,
        created_at=obj.created_at.isoformat() if obj.created_at else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/queue", response_model=ReviewQueueResponse)
def review_queue(
    request: Request,
    program_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """Get the review queue — all rules grouped by status."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        from orchestration.government_sync import get_review_queue
        return get_review_queue(session, program_id)


@router.get("", response_model=list[RuleResponse])
def list_rules(
    request: Request,
    status: str | None = None,
    program_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List rules, optionally filtered by status and program."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        if status:
            objs = repo.list_by_status(status, program_id)
        else:
            from db.models import Rule as RuleORM
            from sqlalchemy import select
            stmt = select(RuleORM)
            if program_id:
                stmt = stmt.where(RuleORM.program_id == program_id)
            objs = list(session.execute(stmt).scalars())
        return [_orm_to_response(o) for o in objs]


# NOTE: /batch/* routes MUST be registered before any /{rule_id}/* route.
# Starlette matches in registration order, so a late /batch/validate would be
# swallowed by POST /{rule_id}/validate with rule_id="batch". Do not reorder.

@router.post("/batch/validate", response_model=list[RuleResponse])
def batch_validate(
    body: BatchActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Batch DRAFT → VALIDATED."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        results = []
        for rule_id in body.rule_ids:
            try:
                obj = repo.validate_rule(rule_id, actor=actor)
                repo.save_version_snapshot(rule_id, "VALIDATED", actor)
                results.append(_orm_to_response(obj))
            except ValueError as e:
                logger.warning("Batch validate failed for rule",
                               extra={"rule_id": rule_id, "error": str(e)})
        return results


@router.post("/batch/approve", response_model=list[RuleResponse])
def batch_approve(
    body: BatchActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Batch VALIDATED → APPROVED."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        results = []
        for rule_id in body.rule_ids:
            try:
                obj = repo.approve_rule(rule_id, actor=actor)
                repo.save_version_snapshot(rule_id, "APPROVED", actor)
                results.append(_orm_to_response(obj))
            except ValueError as e:
                logger.warning("Batch approve failed for rule",
                               extra={"rule_id": rule_id, "error": str(e)})
        return results


@router.post("/batch/activate", response_model=list[RuleResponse])
def batch_activate(
    body: BatchActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Batch APPROVED → ACTIVE (each transactionally supersedes old version)."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        results = []
        for rule_id in body.rule_ids:
            try:
                obj = repo.activate_rule(rule_id, actor=actor)
                results.append(_orm_to_response(obj))
            except ValueError as e:
                logger.warning("Batch activate failed for rule",
                               extra={"rule_id": rule_id, "error": str(e)})
        return results


@router.get("/{rule_id}", response_model=RuleResponse)
def get_rule(
    rule_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Get a single rule by ID."""
    _require_admin(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        obj = repo.get(rule_id)
        if not obj:
            raise HTTPException(status_code=404, detail="Rule not found")
        return _orm_to_response(obj)


@router.post("/{rule_id}/validate", response_model=RuleResponse)
def validate_rule(
    rule_id: str,
    body: RuleActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """DRAFT → VALIDATED (admin confirms automated validation passed)."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        try:
            obj = repo.validate_rule(rule_id, actor=actor)
            repo.save_version_snapshot(rule_id, "VALIDATED", actor)
            return _orm_to_response(obj)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rule_id}/approve", response_model=RuleResponse)
def approve_rule(
    rule_id: str,
    body: RuleActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """VALIDATED → APPROVED (human review passes)."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        try:
            obj = repo.approve_rule(rule_id, actor=actor)
            repo.save_version_snapshot(rule_id, "APPROVED", actor)
            return _orm_to_response(obj)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rule_id}/reject", response_model=RuleResponse)
def reject_rule(
    rule_id: str,
    body: RuleActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Any pre-active status → REJECTED."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        try:
            obj = repo.reject_rule(rule_id, actor=actor)
            repo.save_version_snapshot(rule_id, "REJECTED", actor)
            return _orm_to_response(obj)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rule_id}/activate", response_model=RuleResponse)
def activate_rule(
    rule_id: str,
    body: RuleActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """APPROVED → ACTIVE. Transactionally supersedes any existing active version."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        try:
            obj = repo.activate_rule(rule_id, actor=actor)
            return _orm_to_response(obj)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rule_id}/retire", response_model=RuleResponse)
def retire_rule(
    rule_id: str,
    body: RuleActionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """SUPERSEDED/ACTIVE → RETIRED."""
    _require_admin(current_user)
    actor = _actor_from(current_user)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        repo = EnhancedRuleRepo(session)
        try:
            obj = repo.retire_rule(rule_id, actor=actor)
            repo.save_version_snapshot(rule_id, "RETIRED", actor)
            return _orm_to_response(obj)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
