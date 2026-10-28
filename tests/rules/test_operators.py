"""Tests for Phase 3A operators."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules.operators import evaluate_operator, SUPPORTED_OPERATORS


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


# ---------------------------------------------------------------------------
# Ordinal scale comparison (english_level etc.) — LT/LTE/GT/GTE on qualitative
# strings must use real-world ranking, not raw lexicographic string comparison.
# "vocational" vs "competent" is the proof case: alphabetically 'v' > 'c', so
# plain Python string comparison says "vocational" >= "competent" is True —
# but vocational is actually the LOWER level (none < vocational < competent <
# proficient < superior), so the correct answer is False.
# ---------------------------------------------------------------------------

def test_ordinal_disagrees_with_lexicographic_vocational_vs_competent():
    # Raw string comparison would (wrongly) say True here.
    assert "vocational" >= "competent"
    passed, reason = evaluate_operator("GTE", "vocational", "competent")
    assert passed is False, "vocational is ranked BELOW competent, GTE must fail"


def test_ordinal_proficient_meets_competent_requirement():
    passed, reason = evaluate_operator("GTE", "proficient", "competent")
    assert passed is True


def test_ordinal_case_insensitive():
    passed, reason = evaluate_operator("GTE", "PROFICIENT", "competent")
    assert passed is True


def test_ordinal_equal_levels_pass_gte():
    passed, reason = evaluate_operator("GTE", "competent", "competent")
    assert passed is True


def test_ordinal_lt_still_works_for_lower_level():
    passed, reason = evaluate_operator("LT", "vocational", "competent")
    assert passed is True


def test_non_ordinal_strings_fall_back_to_type_error_unchanged():
    """A string that isn't a recognized ordinal level must behave exactly as
    before this fix (TypeError on comparison against a number), not silently
    match some scale."""
    passed, reason = evaluate_operator("LT", "not_a_level", 45)
    assert passed is False
    assert "Type error" in reason


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
