"""Rule activation status machine and transactional workflow.

Validates state transitions, enforces single-active-version per rule,
and provides the transactional activate/supersede logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)


class RuleLifecycleStatus(str, Enum):
    """Strict lifecycle for rule versions. Must follow: DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED → RETIRED."""
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


# Allowed transitions: from_status → set of allowed to_statuses
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"APPROVED", "REJECTED", "DRAFT"},
    "APPROVED": {"ACTIVE", "REJECTED", "VALIDATED"},
    "ACTIVE": {"SUPERSEDED", "RETIRED"},
    "SUPERSEDED": {"RETIRED"},
    "RETIRED": set(),
    "REJECTED": {"DRAFT"},
}


def can_transition(from_status: str, to_status: str) -> bool:
    """Check whether a status transition is allowed."""
    return to_status in _VALID_TRANSITIONS.get(from_status, set())


def validate_transition(from_status: str, to_status: str) -> None:
    """Raise ValueError if transition is not allowed."""
    if not can_transition(from_status, to_status):
        raise ValueError(
            f"Invalid rule status transition: {from_status} → {to_status}. "
            f"Allowed: {sorted(_VALID_TRANSITIONS.get(from_status, set()))}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuleActivationError(Exception):
    """Raised when activation fails (e.g. compilation error, invalid transition)."""
    pass
