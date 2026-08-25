"""Tests for Phase 3A operators."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.operators import evaluate_operator, SUPPORTED_OPERATORS


def test_supported_operators_count():
    assert len(SUPPORTED_OPERATORS) == 12


def test_eq_pass():
    passed, reason = evaluate_operator("EQ", 45, 45)
    assert passed is True


def test_eq_fail():
    passed, reason = evaluate_operator("EQ", 45, 50)
    assert passed is False


def test_neq_pass():
    passed, reason = evaluate_operator("NEQ", 45, 50)
    assert passed is True


def test_neq_fail():
    passed, reason = evaluate_operator("NEQ", 45, 45)
    assert passed is False


def test_lt_pass():
    passed, reason = evaluate_operator("LT", 30, 45)
    assert passed is True


def test_lt_fail():
    passed, reason = evaluate_operator("LT", 50, 45)
    assert passed is False


def test_lte_pass():
    passed, reason = evaluate_operator("LTE", 45, 45)
    assert passed is True


def test_lte_fail():
    passed, reason = evaluate_operator("LTE", 46, 45)
    assert passed is False


def test_gt_pass():
    passed, reason = evaluate_operator("GT", 70, 65)
    assert passed is True


def test_gt_fail():
    passed, reason = evaluate_operator("GT", 60, 65)
    assert passed is False


def test_gte_pass():
    passed, reason = evaluate_operator("GTE", 65, 65)
    assert passed is True


def test_gte_fail():
    passed, reason = evaluate_operator("GTE", 64, 65)
    assert passed is False


def test_in_pass():
    passed, reason = evaluate_operator("IN", "substantive", ["substantive", "BVA"])
    assert passed is True


def test_in_fail():
    passed, reason = evaluate_operator("IN", "tourist", ["substantive", "BVA"])
    assert passed is False


def test_not_in_pass():
    passed, reason = evaluate_operator("NOT_IN", "tourist", ["substantive", "BVA"])
    assert passed is True


def test_not_in_fail():
    passed, reason = evaluate_operator("NOT_IN", "substantive", ["substantive", "BVA"])
    assert passed is False


def test_contains_pass():
    passed, reason = evaluate_operator("CONTAINS", "skilled occupation list", "occupation")
    assert passed is True


def test_contains_fail():
    passed, reason = evaluate_operator("CONTAINS", "skilled occupation list", "nomination")
    assert passed is False


def test_not_contains_pass():
    passed, reason = evaluate_operator("NOT_CONTAINS", "skilled occupation list", "nomination")
    assert passed is True


def test_not_contains_fail():
    passed, reason = evaluate_operator("NOT_CONTAINS", "skilled occupation list", "occupation")
    assert passed is False


def test_exists_pass():
    passed, reason = evaluate_operator("EXISTS", "value", None)
    assert passed is True


def test_exists_fail():
    passed, reason = evaluate_operator("EXISTS", None, None)
    assert passed is False


def test_not_exists_pass():
    passed, reason = evaluate_operator("NOT_EXISTS", None, None)
    assert passed is True


def test_not_exists_fail():
    passed, reason = evaluate_operator("NOT_EXISTS", "value", None)
    assert passed is False


def test_none_value_for_comparison():
    passed, reason = evaluate_operator("LT", None, 45)
    assert passed is False
    assert "None" in reason


def test_unsupported_operator():
    passed, reason = evaluate_operator("BETWEEN", 5, 10)
    assert passed is False
    assert "Unsupported" in reason


def test_type_error_handling():
    passed, reason = evaluate_operator("LT", "not_a_number", 45)
    assert passed is False
    assert "Type error" in reason
