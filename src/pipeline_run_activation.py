"""PipelineRun status state machine with strict transitions.

Validates that PipelineRun status transitions are legal:
    RUNNING → COMPLETED | FAILED | PARTIAL

Once terminal (COMPLETED/FAILED/PARTIAL), no further transitions are allowed.
This prevents illegal state changes like COMPLETED → RUNNING.
"""
from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    """Strict lifecycle for pipeline runs."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


# Allowed transitions: from_status → set of allowed to_statuses
_VALID_RUN_TRANSITIONS: dict[str, set[str]] = {
    "RUNNING": {"COMPLETED", "FAILED", "PARTIAL"},
    "COMPLETED": set(),
    "FAILED": set(),
    "PARTIAL": set(),
}


def can_run_transition(from_status: str, to_status: str) -> bool:
    """Check whether a PipelineRun status transition is allowed."""
    return to_status in _VALID_RUN_TRANSITIONS.get(from_status, set())


def validate_run_transition(from_status: str, to_status: str) -> None:
    """Raise ValueError if transition is not allowed."""
    if not can_run_transition(from_status, to_status):
        raise ValueError(
            f"Invalid PipelineRun status transition: {from_status} → {to_status}. "
            f"Allowed: {sorted(_VALID_RUN_TRANSITIONS.get(from_status, set()))}"
        )
