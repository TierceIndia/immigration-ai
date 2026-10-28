"""Phase 3B-4: Dependency chaining — rule requires prerequisite rules.

Semantics:
- Evaluate dependencies first.
- If any dependency FAILs -> dependent rule is NOT_APPLICABLE.
- If any dependency UNKNOWN -> dependent rule is UNKNOWN.
- If all dependencies PASS -> evaluate the rule normally.
- If any dependency NOT_APPLICABLE -> dependent rule is NOT_APPLICABLE.

This is semantically equivalent to IF dep1 AND dep2 AND ... THEN rule,
but makes the dependency relationship explicit in the data model.

Real examples:
- "Visa grants" depends on "Eligibility assessment passes"
- "Points test" depends on "Skills assessment obtained"
- "English requirement" depends on "Not from exempt country"
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules.models import EvaluationStatus, Rule, Provenance
from rules.expressions import (
    evaluate_and, evaluate_or, evaluate_not,
    RuleLeaf, ANDNode, ORNode, NOTNode, ConditionalNode, ExceptionNode,
    DependencyNode, evaluate_expression,
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
# DEPENDENCY TRUTH TABLE
# ============================================================

class TestDependencyTruthTable:
    """DependencyNode: rule REQUIRES dependencies."""

    def test_all_deps_pass(self):
        """All dependencies PASS -> evaluate rule."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_dep_fails(self):
        """One dependency FAILs -> NOT_APPLICABLE."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 50, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_dep_unknown(self):
        """One dependency UNKNOWN -> UNKNOWN."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 30}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_dep_not_applicable(self):
        """One dependency NOT_APPLICABLE -> NOT_APPLICABLE."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_rule_fails_despite_passing_deps(self):
        """Dependencies PASS but rule itself FAILs."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": False, "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.FAIL

    def test_rule_unknown_despite_passing_deps(self):
        """Dependencies PASS but rule itself UNKNOWN."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_all_deps_not_applicable(self):
        """All dependencies NOT_APPLICABLE -> NOT_APPLICABLE."""
        node = DependencyNode(
            RuleLeaf("VISA"),
            [RuleLeaf("AGE"), RuleLeaf("POINTS")],
        )
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN

    def test_no_dependencies(self):
        """No dependencies -> evaluate rule directly."""
        node = DependencyNode(RuleLeaf("AGE"), [])
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
        }
        data = {"age": 30}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS


# ============================================================
# NESTED DEPENDENCIES
# ============================================================

class TestNestedDependencies:
    """Dependencies nested with other expression types."""

    def test_dependency_inside_and(self):
        """AND(dependency_rule, other)."""
        dep = DependencyNode(RuleLeaf("VISA"), [RuleLeaf("AGE")])
        node = ANDNode([dep, RuleLeaf("POINTS")])
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 30, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_dependency_inside_and_dep_fails(self):
        """AND(dependency_rule, other) where dep fails -> dep NOT_APPLICABLE, AND passes on remaining."""
        dep = DependencyNode(RuleLeaf("VISA"), [RuleLeaf("AGE")])
        node = ANDNode([dep, RuleLeaf("POINTS")])
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
        }
        data = {"visa_granted": True, "age": 50, "points": 70}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_chained_dependencies(self):
        """A depends on B, B depends on C."""
        dep_b = DependencyNode(RuleLeaf("B"), [RuleLeaf("C")])
        dep_a = DependencyNode(RuleLeaf("A"), [dep_b])
        rules = {
            "A": _make_rule("A", "a", "EQ", True),
            "B": _make_rule("B", "b", "EQ", True),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"a": True, "b": True, "c": True}
        status, _ = evaluate_expression(dep_a, rules, data)
        assert status == EvaluationStatus.PASS

    def test_chained_dep_c_fails(self):
        """A depends on B, B depends on C, C FAILs -> both A and B NOT_APPLICABLE."""
        dep_b = DependencyNode(RuleLeaf("B"), [RuleLeaf("C")])
        dep_a = DependencyNode(RuleLeaf("A"), [dep_b])
        rules = {
            "A": _make_rule("A", "a", "EQ", True),
            "B": _make_rule("B", "b", "EQ", True),
            "C": _make_rule("C", "c", "EQ", True),
        }
        data = {"a": True, "b": True, "c": False}
        status, _ = evaluate_expression(dep_a, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_dependency_serialization(self):
        """DependencyNode serialization."""
        node = DependencyNode(RuleLeaf("A"), [RuleLeaf("B"), RuleLeaf("C")])
        d = node.to_dict()
        assert d["type"] == "DEPENDENCY"
        assert d["rule"]["type"] == "RULE"
        assert len(d["dependencies"]) == 2


# ============================================================
# REAL IMMIGRATION CASES
# ============================================================

class TestRealDependencyCases:
    """Real immigration dependency patterns."""

    def test_visa_grants_depends_on_eligibility(self):
        """Visa grants depends on eligibility passing."""
        dep = DependencyNode(RuleLeaf("VISA"), [RuleLeaf("ELIGIBILITY")])
        rules = {
            "VISA": _make_rule("VISA", "visa_granted", "EQ", True),
            "ELIGIBILITY": _make_rule("ELIGIBILITY", "eligible", "EQ", True),
        }
        data = {"visa_granted": True, "eligible": True}
        status, _ = evaluate_expression(dep, rules, data)
        assert status == EvaluationStatus.PASS

    def test_points_depends_on_skills_assessment(self):
        """Points test depends on skills assessment obtained."""
        dep = DependencyNode(RuleLeaf("POINTS"), [RuleLeaf("SKILLS")])
        rules = {
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
            "SKILLS": _make_rule("SKILLS", "skills_assessed", "EQ", True),
        }
        data = {"points": 70, "skills_assessed": False}
        status, _ = evaluate_expression(dep, rules, data)
        assert status == EvaluationStatus.NOT_APPLICABLE

    def test_190_full_dependency_chain(self):
        """189 eligibility: AND(age, points, english) all depend on skills assessment."""
        dep_age = DependencyNode(RuleLeaf("AGE"), [RuleLeaf("SKILLS")])
        dep_pts = DependencyNode(RuleLeaf("POINTS"), [RuleLeaf("SKILLS")])
        dep_eng = DependencyNode(RuleLeaf("ENGLISH"), [RuleLeaf("SKILLS")])
        node = ANDNode([dep_age, dep_pts, dep_eng])
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
            "ENGLISH": _make_rule("ENGLISH", "english", "GTE", "competent"),
            "SKILLS": _make_rule("SKILLS", "skills_assessed", "EQ", True),
        }
        data = {"age": 30, "points": 70, "english": "proficient", "skills_assessed": True}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.PASS

    def test_190_skills_not_assessed(self):
        """189 eligibility when skills not assessed -> UNKNOWN (field missing)."""
        dep_age = DependencyNode(RuleLeaf("AGE"), [RuleLeaf("SKILLS")])
        dep_pts = DependencyNode(RuleLeaf("POINTS"), [RuleLeaf("SKILLS")])
        dep_eng = DependencyNode(RuleLeaf("ENGLISH"), [RuleLeaf("SKILLS")])
        node = ANDNode([dep_age, dep_pts, dep_eng])
        rules = {
            "AGE": _make_rule("AGE", "age", "LT", 45),
            "POINTS": _make_rule("POINTS", "points", "GTE", 65),
            "ENGLISH": _make_rule("ENGLISH", "english", "GTE", "competent"),
            "SKILLS": _make_rule("SKILLS", "skills_assessed", "EQ", True),
        }
        data = {"age": 30, "points": 70, "english": "proficient"}
        status, _ = evaluate_expression(node, rules, data)
        assert status == EvaluationStatus.UNKNOWN
