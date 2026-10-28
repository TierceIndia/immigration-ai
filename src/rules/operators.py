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

# Ordered qualitative scales used by LT/LTE/GT/GTE. Government eligibility text
# documents these as ranked levels (e.g. "competent English or higher" — see
# prompts/requirement_extraction_v2.md and ApplicantProfile.english_level's
# documented "none|vocational|competent|proficient|superior" ordering), but raw
# Python string comparison (">=" on str) is lexicographic, not ordinal — it gives
# wrong answers whenever alphabetical order doesn't match real-world ranking
# (e.g. "vocational" >= "competent" is True lexicographically, but vocational is
# actually the LOWER level). When both operands are found in the same registered
# scale below, comparison uses the scale's rank instead of raw string comparison.
_ORDINAL_SCALES: tuple[dict[str, int], ...] = (
    {"none": 0, "vocational": 1, "competent": 2, "proficient": 3, "superior": 4},  # english_level
)


def _ordinal_rank(value: Any) -> int | None:
    """Return value's rank in whichever registered ordinal scale contains it
    (case-insensitive), or None if it isn't a recognized ordinal level."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    for scale in _ORDINAL_SCALES:
        if key in scale:
            return scale[key]
    return None


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

    # Ordinal-scale comparison for qualitative levels (see _ORDINAL_SCALES):
    # only kicks in when BOTH operands resolve to a rank in the same known
    # scale, so plain numeric/string EQ/NEQ/IN/CONTAINS comparisons elsewhere
    # are entirely unaffected.
    if operator in ("LT", "LTE", "GT", "GTE"):
        actual_rank = _ordinal_rank(actual)
        expected_rank = _ordinal_rank(expected)
        if actual_rank is not None and expected_rank is not None:
            actual, expected = actual_rank, expected_rank

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
