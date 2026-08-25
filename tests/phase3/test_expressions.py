"""Phase 3B-1: AND/OR/NOT truth table tests.

These tests ARE the specification. They define the exact semantics
before any engine code is written.

AND semantics:
- PASS + PASS -> PASS
- PASS + FAIL -> FAIL
- FAIL + PASS -> FAIL
- FAIL + FAIL -> FAIL
- PASS + UNKNOWN -> UNKNOWN
- UNKNOWN + PASS -> UNKNOWN
- UNKNOWN + UNKNOWN -> UNKNOWN
- FAIL + UNKNOWN -> FAIL  (FAIL takes precedence over UNKNOWN)

OR semantics:
- PASS + PASS -> PASS
- PASS + FAIL -> PASS
- FAIL + PASS -> PASS
- FAIL + FAIL -> FAIL
- PASS + UNKNOWN -> PASS
- UNKNOWN + PASS -> PASS
- UNKNOWN + UNKNOWN -> UNKNOWN
- FAIL + UNKNOWN -> UNKNOWN

NOT semantics:
- PASS -> FAIL
- FAIL -> PASS
- UNKNOWN -> UNKNOWN
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.models import EvaluationStatus


# ============================================================
# AND TRUTH TABLE
# ============================================================

class TestANDTruthTable:
    """A AND B semantics for immigration eligibility.

    Key rule: FAIL takes precedence over UNKNOWN.
    If one mandatory requirement fails, the applicant cannot
    become eligible simply because another requirement is unknown.
    """

    def test_and_pass_pass(self):
        assert self._and(EvaluationStatus.PASS, EvaluationStatus.PASS) == EvaluationStatus.PASS

    def test_and_pass_fail(self):
        assert self._and(EvaluationStatus.PASS, EvaluationStatus.FAIL) == EvaluationStatus.FAIL

    def test_and_fail_pass(self):
        assert self._and(EvaluationStatus.FAIL, EvaluationStatus.PASS) == EvaluationStatus.FAIL

    def test_and_fail_fail(self):
        assert self._and(EvaluationStatus.FAIL, EvaluationStatus.FAIL) == EvaluationStatus.FAIL

    def test_and_pass_unknown(self):
        assert self._and(EvaluationStatus.PASS, EvaluationStatus.UNKNOWN) == EvaluationStatus.UNKNOWN

    def test_and_unknown_pass(self):
        assert self._and(EvaluationStatus.UNKNOWN, EvaluationStatus.PASS) == EvaluationStatus.UNKNOWN

    def test_and_unknown_unknown(self):
        assert self._and(EvaluationStatus.UNKNOWN, EvaluationStatus.UNKNOWN) == EvaluationStatus.UNKNOWN

    def test_and_fail_unknown(self):
        """FAIL takes precedence over UNKNOWN."""
        assert self._and(EvaluationStatus.FAIL, EvaluationStatus.UNKNOWN) == EvaluationStatus.FAIL

    def test_and_unknown_fail(self):
        """FAIL takes precedence over UNKNOWN."""
        assert self._and(EvaluationStatus.UNKNOWN, EvaluationStatus.FAIL) == EvaluationStatus.FAIL

    def _and(self, a: EvaluationStatus, b: EvaluationStatus) -> EvaluationStatus:
        from phase3.expressions import evaluate_and
        return evaluate_and([a, b])


# ============================================================
# OR TRUTH TABLE
# ============================================================

class TestORTruthTable:
    """A OR B semantics for immigration eligibility.

    OR means at least one path must pass.
    UNKNOWN only matters if ALL children are UNKNOWN.
    FAIL only results if ALL children are FAIL.
    """

    def test_or_pass_pass(self):
        assert self._or(EvaluationStatus.PASS, EvaluationStatus.PASS) == EvaluationStatus.PASS

    def test_or_pass_fail(self):
        assert self._or(EvaluationStatus.PASS, EvaluationStatus.FAIL) == EvaluationStatus.PASS

    def test_or_fail_pass(self):
        assert self._or(EvaluationStatus.FAIL, EvaluationStatus.PASS) == EvaluationStatus.PASS

    def test_or_fail_fail(self):
        assert self._or(EvaluationStatus.FAIL, EvaluationStatus.FAIL) == EvaluationStatus.FAIL

    def test_or_pass_unknown(self):
        assert self._or(EvaluationStatus.PASS, EvaluationStatus.UNKNOWN) == EvaluationStatus.PASS

    def test_or_unknown_pass(self):
        assert self._or(EvaluationStatus.UNKNOWN, EvaluationStatus.PASS) == EvaluationStatus.PASS

    def test_or_unknown_unknown(self):
        assert self._or(EvaluationStatus.UNKNOWN, EvaluationStatus.UNKNOWN) == EvaluationStatus.UNKNOWN

    def test_or_fail_unknown(self):
        """FAIL + UNKNOWN -> UNKNOWN (can't decide without UNKNOWN resolving)."""
        assert self._or(EvaluationStatus.FAIL, EvaluationStatus.UNKNOWN) == EvaluationStatus.UNKNOWN

    def test_or_unknown_fail(self):
        """UNKNOWN + FAIL -> UNKNOWN."""
        assert self._or(EvaluationStatus.UNKNOWN, EvaluationStatus.FAIL) == EvaluationStatus.UNKNOWN

    def _or(self, a: EvaluationStatus, b: EvaluationStatus) -> EvaluationStatus:
        from phase3.expressions import evaluate_or
        return evaluate_or([a, b])


# ============================================================
# NOT TRUTH TABLE
# ============================================================

class TestNOTTruthTable:
    """NOT inverts PASS/FAIL, UNKNOWN stays UNKNOWN."""

    def test_not_pass(self):
        assert self._not(EvaluationStatus.PASS) == EvaluationStatus.FAIL

    def test_not_fail(self):
        assert self._not(EvaluationStatus.FAIL) == EvaluationStatus.PASS

    def test_not_unknown(self):
        assert self._not(EvaluationStatus.UNKNOWN) == EvaluationStatus.UNKNOWN

    def _not(self, a: EvaluationStatus) -> EvaluationStatus:
        from phase3.expressions import evaluate_not
        return evaluate_not(a)


# ============================================================
# AND/OR THREE-WAY
# ============================================================

class TestANDThreeWay:
    """AND with three inputs."""

    def test_all_pass(self):
        from phase3.expressions import evaluate_and
        result = evaluate_and([
            EvaluationStatus.PASS,
            EvaluationStatus.PASS,
            EvaluationStatus.PASS,
        ])
        assert result == EvaluationStatus.PASS

    def test_one_fail_among_passes(self):
        from phase3.expressions import evaluate_and
        result = evaluate_and([
            EvaluationStatus.PASS,
            EvaluationStatus.FAIL,
            EvaluationStatus.PASS,
        ])
        assert result == EvaluationStatus.FAIL

    def test_one_unknown_among_passes(self):
        from phase3.expressions import evaluate_and
        result = evaluate_and([
            EvaluationStatus.PASS,
            EvaluationStatus.UNKNOWN,
            EvaluationStatus.PASS,
        ])
        assert result == EvaluationStatus.UNKNOWN

    def test_fail_and_unknown_mixed(self):
        from phase3.expressions import evaluate_and
        result = evaluate_and([
            EvaluationStatus.PASS,
            EvaluationStatus.FAIL,
            EvaluationStatus.UNKNOWN,
        ])
        assert result == EvaluationStatus.FAIL


class TestORThreeWay:
    """OR with three inputs."""

    def test_all_pass(self):
        from phase3.expressions import evaluate_or
        result = evaluate_or([
            EvaluationStatus.PASS,
            EvaluationStatus.PASS,
            EvaluationStatus.PASS,
        ])
        assert result == EvaluationStatus.PASS

    def test_one_pass_among_fails(self):
        from phase3.expressions import evaluate_or
        result = evaluate_or([
            EvaluationStatus.FAIL,
            EvaluationStatus.PASS,
            EvaluationStatus.FAIL,
        ])
        assert result == EvaluationStatus.PASS

    def test_one_unknown_among_fails(self):
        from phase3.expressions import evaluate_or
        result = evaluate_or([
            EvaluationStatus.FAIL,
            EvaluationStatus.UNKNOWN,
            EvaluationStatus.FAIL,
        ])
        assert result == EvaluationStatus.UNKNOWN

    def test_all_unknown(self):
        from phase3.expressions import evaluate_or
        result = evaluate_or([
            EvaluationStatus.UNKNOWN,
            EvaluationStatus.UNKNOWN,
            EvaluationStatus.UNKNOWN,
        ])
        assert result == EvaluationStatus.UNKNOWN


# ============================================================
# NESTED EXPRESSIONS
# ============================================================

class TestNestedExpressions:
    """(A AND B) OR (C AND D) style nesting."""

    def test_and_of_ors(self):
        """(PASS OR FAIL) AND (PASS OR PASS) -> PASS AND PASS -> PASS."""
        from phase3.expressions import evaluate_and, evaluate_or
        left = evaluate_or([EvaluationStatus.PASS, EvaluationStatus.FAIL])
        right = evaluate_or([EvaluationStatus.PASS, EvaluationStatus.PASS])
        result = evaluate_and([left, right])
        assert result == EvaluationStatus.PASS

    def test_or_of_ands_with_unknown(self):
        """(PASS AND UNKNOWN) OR (FAIL AND FAIL) -> UNKNOWN OR FAIL -> UNKNOWN."""
        from phase3.expressions import evaluate_and, evaluate_or
        left = evaluate_and([EvaluationStatus.PASS, EvaluationStatus.UNKNOWN])
        right = evaluate_and([EvaluationStatus.FAIL, EvaluationStatus.FAIL])
        result = evaluate_or([left, right])
        assert result == EvaluationStatus.UNKNOWN

    def test_deep_nesting(self):
        """((PASS AND PASS) OR FAIL) AND PASS -> PASS AND PASS -> PASS."""
        from phase3.expressions import evaluate_and, evaluate_or
        inner_and = evaluate_and([EvaluationStatus.PASS, EvaluationStatus.PASS])
        inner_or = evaluate_or([inner_and, EvaluationStatus.FAIL])
        result = evaluate_and([inner_or, EvaluationStatus.PASS])
        assert result == EvaluationStatus.PASS

    def test_deep_nesting_with_unknown(self):
        """((PASS AND UNKNOWN) OR FAIL) AND PASS -> UNKNOWN OR FAIL -> UNKNOWN, then UNKNOWN AND PASS -> UNKNOWN."""
        from phase3.expressions import evaluate_and, evaluate_or
        inner_and = evaluate_and([EvaluationStatus.PASS, EvaluationStatus.UNKNOWN])
        inner_or = evaluate_or([inner_and, EvaluationStatus.FAIL])
        result = evaluate_and([inner_or, EvaluationStatus.PASS])
        assert result == EvaluationStatus.UNKNOWN
