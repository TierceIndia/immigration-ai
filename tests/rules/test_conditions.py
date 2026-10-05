"""Phase 3B-2: IF/THEN conditional rules + NOT_APPLICABLE semantics.

These tests ARE the specification.

IF/THEN truth table:
- IF PASS    THEN PASS    -> PASS
- IF PASS    THEN FAIL    -> FAIL
- IF PASS    THEN UNKNOWN -> UNKNOWN
- IF FAIL    THEN *       -> NOT_APPLICABLE
- IF UNKNOWN THEN *       -> UNKNOWN
- IF NOT_APPLICABLE THEN * -> NOT_APPLICABLE

NOT_APPLICABLE in AND/OR:
- AND: Filter out NOT_APPLICABLE, evaluate rest. All NOT_APPLICABLE -> NOT_APPLICABLE.
- OR: Filter out NOT_APPLICABLE, evaluate rest. All NOT_APPLICABLE -> NOT_APPLICABLE.
- NOT: NOT_APPLICABLE stays NOT_APPLICABLE.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules.models import EvaluationStatus, Rule, Provenance
from rules.expressions import (
    evaluate_and, evaluate_or, evaluate_not,
    RuleLeaf, ANDNode, ORNode, NOTNode, ConditionalNode,
    evaluate_expression,
)


def _make_rule(rule_id, field, op, val):
    return Rule(
        rule_id=rule_id, requirement_id=f"REQ-{rule_id}",
        program_id="SKILLED_189", category="TEST",
        field=field, operator=op, value=val, unit=None,
        mandatory=True, critical=True, message=f"Rule {rule_id}",
        provenance=Provenance(
            source_id="SRC", document_id="DOC", document_version=1,
            chunk_id=1, requirement_id=f"REQ-{rule_id}", evidence_text="evidence",
        ),
    )


# ============================================================
# IF/THEN TRUTH TABLE
# ============================================================

class TestIFThenTruthTable:
    """IF condition THEN consequence semantics."""

    def test_if_pass_then_pass(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 30, "points": 70}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_if_pass_then_fail(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 30, "points": 60}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.FAIL

    def test_if_pass_then_unknown(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 30}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_if_fail_then_pass(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 50, "points": 70}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_if_fail_then_fail(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 50, "points": 60}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_if_fail_then_unknown(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 50}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_if_unknown_then_pass(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"points": 70}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_if_unknown_then_fail(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"points": 60}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_if_unknown_then_unknown(self):
        cond = RuleLeaf("AGE")
        cons = RuleLeaf("POINTS")
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {}
        node = ConditionalNode(cond, cons)
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN


# ============================================================
# NOT_APPLICABLE IN AND/OR
# ============================================================

class TestNotApplicableInAND:
    """NOT_APPLICABLE filtered out in AND evaluation."""

    def test_and_with_not_applicable(self):
        """PASS + NOT_APPLICABLE -> PASS (NOT_APPLICABLE filtered,只剩 PASS)."""
        result = evaluate_and([EvaluationStatus.PASS, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.PASS

    def test_and_fail_with_not_applicable(self):
        """FAIL + NOT_APPLICABLE -> FAIL."""
        result = evaluate_and([EvaluationStatus.FAIL, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.FAIL

    def test_and_unknown_with_not_applicable(self):
        """UNKNOWN + NOT_APPLICABLE -> UNKNOWN."""
        result = evaluate_and([EvaluationStatus.UNKNOWN, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.UNKNOWN

    def test_and_all_not_applicable(self):
        """All NOT_APPLICABLE -> NOT_APPLICABLE."""
        result = evaluate_and([
            EvaluationStatus.NOT_APPLICABLE,
            EvaluationStatus.NOT_APPLICABLE,
        ])
        assert result == EvaluationStatus.NOT_APPLICABLE

    def test_and_mixed_with_not_applicable(self):
        """PASS + FAIL + NOT_APPLICABLE -> FAIL."""
        result = evaluate_and([
            EvaluationStatus.PASS,
            EvaluationStatus.FAIL,
            EvaluationStatus.NOT_APPLICABLE,
        ])
        assert result == EvaluationStatus.FAIL


class TestNotApplicableInOR:
    """NOT_APPLICABLE filtered out in OR evaluation."""

    def test_or_with_not_applicable(self):
        """PASS + NOT_APPLICABLE -> PASS."""
        result = evaluate_or([EvaluationStatus.PASS, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.PASS

    def test_or_fail_with_not_applicable(self):
        """FAIL + NOT_APPLICABLE -> FAIL (only FAIL remains)."""
        result = evaluate_or([EvaluationStatus.FAIL, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.FAIL

    def test_or_unknown_with_not_applicable(self):
        """UNKNOWN + NOT_APPLICABLE -> UNKNOWN."""
        result = evaluate_or([EvaluationStatus.UNKNOWN, EvaluationStatus.NOT_APPLICABLE])
        assert result == EvaluationStatus.UNKNOWN

    def test_or_all_not_applicable(self):
        """All NOT_APPLICABLE -> NOT_APPLICABLE."""
        result = evaluate_or([
            EvaluationStatus.NOT_APPLICABLE,
            EvaluationStatus.NOT_APPLICABLE,
        ])
        assert result == EvaluationStatus.NOT_APPLICABLE


class TestNotApplicableInNOT:
    """NOT_APPLICABLE stays NOT_APPLICABLE after negation."""

    def test_not_not_applicable(self):
        result = evaluate_not(EvaluationStatus.NOT_APPLICABLE)
        assert result == EvaluationStatus.NOT_APPLICABLE


# ============================================================
# NOT_APPLICABLE IN EXPRESSION TREE
# ============================================================

class TestNotApplicableInExpressionTree:
    """NOT_APPLICABLE propagation through expression trees."""

    def test_and_node_with_not_applicable(self):
        """AND(PASS, IF FAIL THEN X) -> PASS (NOT_APPLICABLE filtered)."""
        cond = ConditionalNode(RuleLeaf("A"), RuleLeaf("B"))
        node = ANDNode([cond, RuleLeaf("C")])
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "LT", 45),
            "C": _make_rule("C", "c", "LT", 45),
        }
        data = {"a": 50, "c": 30}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_or_node_with_not_applicable(self):
        """OR(IF FAIL THEN X, FAIL) -> FAIL (NOT_APPLICABLE filtered)."""
        cond = ConditionalNode(RuleLeaf("A"), RuleLeaf("B"))
        node = ORNode([cond, RuleLeaf("C")])
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "LT", 45),
            "C": _make_rule("C", "c", "LT", 45),
        }
        data = {"a": 50, "c": 50}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.FAIL


# ============================================================
# NESTED CONDITIONALS
# ============================================================

class TestNestedConditionals:
    """IF/THEN nested with AND/OR."""

    def test_if_then_inside_and(self):
        """AND(IF PASS THEN PASS, PASS) -> PASS."""
        inner = ConditionalNode(RuleLeaf("A"), RuleLeaf("B"))
        node = ANDNode([inner, RuleLeaf("C")])
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "GTE", 65),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"a": 30, "b": 70, "c": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_if_then_not_applicable_in_and(self):
        """AND(IF FAIL THEN PASS, PASS) -> AND(NOT_APPLICABLE, PASS) -> PASS."""
        inner = ConditionalNode(RuleLeaf("A"), RuleLeaf("B"))
        node = ANDNode([inner, RuleLeaf("C")])
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "GTE", 65),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"a": 50, "c": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_if_then_unknown_in_and(self):
        """AND(IF UNKNOWN THEN PASS, PASS) -> AND(UNKNOWN, PASS) -> UNKNOWN."""
        inner = ConditionalNode(RuleLeaf("A"), RuleLeaf("B"))
        node = ANDNode([inner, RuleLeaf("C")])
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "GTE", 65),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"c": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_if_then_with_or_consequence(self):
        """IF PASS THEN (B OR C)."""
        consequence = ORNode([RuleLeaf("B"), RuleLeaf("C")])
        node = ConditionalNode(RuleLeaf("A"), consequence)
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "GTE", 65),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"a": 30, "b": 60, "c": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_if_unknown_then_consequence_not_evaluated(self):
        """IF UNKNOWN THEN (B OR C) -> UNKNOWN, B and C not evaluated."""
        consequence = ORNode([RuleLeaf("B"), RuleLeaf("C")])
        node = ConditionalNode(RuleLeaf("A"), consequence)
        rules = {
            "A": _make_rule("A", "a", "LT", 45),
            "B": _make_rule("B", "b", "GTE", 65),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {}
        status, evals = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN
        assert len(evals) == 1


# ============================================================
# REAL IMMIGRATION CASES
# ============================================================

class TestRealImmigrationCases:
    """Test with real 189/190 rule structures."""

    def test_189_eligibility_expression(self):
        """189: AND(AGE, POINTS, ENGLISH, SKILLS)."""
        node = ANDNode([
            RuleLeaf("AGE"), RuleLeaf("POINTS"),
            RuleLeaf("ENGLISH"), RuleLeaf("SKILLS"),
        ])
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
            "ENGLISH": _make_rule("ENGLISH", "english", "GTE", "competent"),
            "SKILLS": _make_rule("SKILLS", "skills", "EQ", True),
        }
        data = {"age": 30, "points": 70, "english": "proficient", "skills": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_189_incomplete_profile(self):
        """189 with missing English -> UNKNOWN."""
        node = ANDNode([
            RuleLeaf("AGE"), RuleLeaf("POINTS"),
            RuleLeaf("ENGLISH"), RuleLeaf("SKILLS"),
        ])
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
            "ENGLISH": _make_rule("ENGLISH", "english", "GTE", "competent"),
            "SKILLS": _make_rule("SKILLS", "skills", "EQ", True),
        }
        data = {"age": 30, "points": 70, "skills": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_190_nomination_conditional(self):
        """190: IF visa==190 THEN nomination_required, AND age, points."""
        nomination = ConditionalNode(
            RuleLeaf("VISA_190"),
            RuleLeaf("NOMINATION"),
        )
        node = ANDNode([
            nomination,
            RuleLeaf("AGE"),
            RuleLeaf("POINTS"),
        ])
        rules = {
            "VISA_190": _make_rule("VISA_190", "visa_subclass", "EQ", "190"),
            "NOMINATION": _make_rule("NOMINATION", "nomination", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_subclass": "190", "nomination": True, "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_189_no_nomination_needed(self):
        """189: IF visa==190 THEN nomination -> NOT_APPLICABLE for 189."""
        nomination = ConditionalNode(
            RuleLeaf("VISA_190"),
            RuleLeaf("NOMINATION"),
        )
        node = ANDNode([
            nomination,
            RuleLeaf("AGE"),
            RuleLeaf("POINTS"),
        ])
        rules = {
            "VISA_190": _make_rule("VISA_190", "visa_subclass", "EQ", "190"),
            "NOMINATION": _make_rule("NOMINATION", "nomination", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_subclass": "189", "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_expression_serialization(self):
        """Expression tree can be serialized to dict."""
        node = ANDNode([
            ConditionalNode(RuleLeaf("A"), RuleLeaf("B")),
            NOTNode(RuleLeaf("C")),
        ])
        d = node.to_dict()
        assert d["type"] == "AND"
        assert d["children"][0]["type"] == "IF_THEN"
        assert d["children"][1]["type"] == "NOT"
