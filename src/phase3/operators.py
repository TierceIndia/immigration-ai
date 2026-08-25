"""Phase 3A operator system.

Deterministic operators that evaluate applicant data against rule values.
No dynamic code execution. No lambdas. Pure interpretation.
"""
from __future__ import annotations

from typing import Any


SUPPORTED_OPERATORS = frozenset({
    "EQ", "NEQ", "LT", "LTE", "GT", "GTE",
    "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS",
    "EXISTS", "NOT_EXISTS",
})


def evaluate_operator(operator: str, actual: Any, expected: Any) -> tuple[bool, str]:
    """Evaluate a single operator against actual and expected values.

    Returns:
        (passed, reason) where reason explains the evaluation.
    """
    if operator not in SUPPORTED_OPERATORS:
        return False, f"Unsupported operator: {operator}"

    if operator == "EXISTS":
        passed = actual is not None
        reason = "Field is present" if passed else "Field is missing"
        return passed, reason

    if operator == "NOT_EXISTS":
        passed = actual is None
        reason = "Field is absent" if passed else "Field is present"
        return passed, reason

    if actual is None:
        return False, "Field value is None"

    try:
        if operator == "EQ":
            passed = actual == expected
            reason = f"Got {actual!r}, expected {expected!r}"
            return passed, reason

        if operator == "NEQ":
            passed = actual != expected
            reason = f"Got {actual!r}, expected not {expected!r}"
            return passed, reason

        if operator == "LT":
            passed = actual < expected
            reason = f"Got {actual!r}, expected < {expected!r}"
            return passed, reason

        if operator == "LTE":
            passed = actual <= expected
            reason = f"Got {actual!r}, expected <= {expected!r}"
            return passed, reason

        if operator == "GT":
            passed = actual > expected
            reason = f"Got {actual!r}, expected > {expected!r}"
            return passed, reason

        if operator == "GTE":
            passed = actual >= expected
            reason = f"Got {actual!r}, expected >= {expected!r}"
            return passed, reason

        if operator == "IN":
            passed = actual in expected
            reason = f"Got {actual!r}, expected in {expected!r}"
            return passed, reason

        if operator == "NOT_IN":
            passed = actual not in expected
            reason = f"Got {actual!r}, expected not in {expected!r}"
            return passed, reason

        if operator == "CONTAINS":
            passed = expected in actual
            reason = f"Got {actual!r}, expected to contain {expected!r}"
            return passed, reason

        if operator == "NOT_CONTAINS":
            passed = expected not in actual
            reason = f"Got {actual!r}, expected not to contain {expected!r}"
            return passed, reason

    except TypeError as e:
        return False, f"Type error during comparison: {e}"

    return False, f"Unhandled operator: {operator}"
