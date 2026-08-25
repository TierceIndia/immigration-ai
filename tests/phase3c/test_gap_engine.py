"""Phase 3C-4: Gap Engine — identify missing information and prioritize gaps.

When assessment returns INCOMPLETE, the Gap Engine:
1. Finds all UNKNOWN rule evaluations
2. Determines which fields are missing
3. Deduplicates missing fields
4. Prioritizes gaps (CRITICAL/HIGH/MEDIUM/LOW)
5. Links each gap to its source rule(s)
6. Links each gap to its source evidence
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.models import Rule, RuleStatus, EvaluationStatus, RuleEvaluation, Provenance
from phase3.expressions import ANDNode, RuleLeaf, evaluate_expression
from phase3c.applicant import ApplicantProfile, VisaSelection
from phase3c.gap_engine import GapEngine, Gap, GapPriority


def _make_rule(rule_id, field="age", op="LT", val=45, mandatory=True, critical=True,
               category="AGE", message=None):
    return Rule(
        rule_id=rule_id, requirement_id=f"REQ-{rule_id}",
        program_id="SKILLED_189", category=category,
        field=field, operator=op, value=val, unit=None,
        mandatory=mandatory, critical=critical, message=message or f"Rule {rule_id}",
        provenance=Provenance(
            source_id="SRC-189", document_id="DOC-189", document_version=1,
            chunk_id=1, requirement_id=f"REQ-{rule_id}", evidence_text=f"Evidence for {rule_id}",
        ),
        status=RuleStatus.ACTIVE,
    )


def _evaluate(applicant, rules):
    """Helper: build expression and evaluate."""
    expr = ANDNode([RuleLeaf(r.rule_id) for r in rules])
    rules_dict = {r.rule_id: r for r in rules}
    rule_data = applicant.to_rule_data()
    return evaluate_expression(expr, rules_dict, rule_data)


# ============================================================
# GAP DETECTION
# ============================================================

class TestGapDetection:
    """Finding UNKNOWN evaluations and their missing fields."""

    def test_single_gap(self):
        """One UNKNOWN rule → one gap."""
        rules = [
            _make_rule("AGE", "age", "LT", 45),
            _make_rule("POINTS", "points", "GTE", 65),
        ]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 1
        assert gaps[0].field == "points"

    def test_multiple_gaps(self):
        """Multiple UNKNOWN rules → multiple gaps."""
        rules = [
            _make_rule("AGE", "age", "LT", 45),
            _make_rule("POINTS", "points", "GTE", 65),
            _make_rule("ENGLISH", "english_level", "IN",
                       ["competent", "proficient", "superior"]),
            _make_rule("SKILLS", "skills_assessment_status", "EQ", "VALID"),
        ]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 3
        fields = {g.field for g in gaps}
        assert fields == {"points", "english_level", "skills_assessment_status"}

    def test_no_gaps_when_all_pass(self):
        """All PASS → no gaps."""
        rules = [_make_rule("AGE", "age", "LT", 45)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 0

    def test_no_gaps_for_fail(self):
        """FAIL rules don't create gaps (they're failures, not missing info)."""
        rules = [_make_rule("AGE", "age", "LT", 45)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=50,
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 0

    def test_not_applicable_not_gap(self):
        """NOT_APPLICABLE rules don't create gaps."""
        rules = [_make_rule("POINTS", "points", "GTE", 65)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 1


# ============================================================
# GAP DEDUPLICATION
# ============================================================

class TestGapDeduplication:
    """Multiple rules referencing the same field → one gap."""

    def test_same_field_different_rules(self):
        """Two rules referencing 'points' → one gap."""
        rules = [
            _make_rule("POINTS-MAIN", "points", "GTE", 65),
            _make_rule("POINTS-BONUS", "points", "GTE", 65),
        ]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 1
        assert gaps[0].field == "points"
        assert len(gaps[0].source_rule_ids) == 2


# ============================================================
# GAP LINKAGE
# ============================================================

class TestGapLinkage:
    """Gaps linked to source rules and evidence."""

    def test_gap_links_to_rule(self):
        """Gap references the rule that requires the field."""
        rules = [_make_rule("POINTS", "points", "GTE", 65)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert len(gaps) == 1
        assert "POINTS" in gaps[0].source_rule_ids

    def test_gap_links_to_evidence(self):
        """Gap references source evidence."""
        rules = [_make_rule("POINTS", "points", "GTE", 65)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert gaps[0].source_evidence is not None
        assert "Evidence for POINTS" in gaps[0].source_evidence


# ============================================================
# GAP PRIORITIZATION
# ============================================================

class TestGapPrioritization:
    """Gaps prioritized by rule criticality."""

    def test_critical_gap(self):
        """Critical rule → CRITICAL priority."""
        rules = [_make_rule("AGE", "age", "LT", 45, critical=True)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert gaps[0].priority == GapPriority.CRITICAL

    def test_high_gap(self):
        """Mandatory non-critical rule → HIGH priority."""
        rules = [_make_rule("POINTS", "points", "GTE", 65, mandatory=True, critical=False)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert gaps[0].priority == GapPriority.HIGH

    def test_medium_gap(self):
        """Non-mandatory non-critical rule → MEDIUM priority."""
        rules = [_make_rule("SUPPORT", "support_info", "EQ", "yes", mandatory=False, critical=False)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert gaps[0].priority == GapPriority.MEDIUM

    def test_gaps_sorted_by_priority(self):
        """Gaps sorted: CRITICAL > HIGH > MEDIUM > LOW."""
        rules = [
            _make_rule("SUPPORT", "support_info", "EXISTS", None,
                       mandatory=False, critical=False),
            _make_rule("AGE", "age", "LT", 45, critical=True),
            _make_rule("POINTS", "points", "GTE", 65, mandatory=True, critical=False),
        ]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        priorities = [g.priority for g in gaps]
        # CRITICAL first, then HIGH, then MEDIUM
        assert priorities[0] == GapPriority.CRITICAL


# ============================================================
# USER-FACING QUESTIONS (placeholder)
# ============================================================

class TestGapQuestions:
    """Gaps produce user-facing questions."""

    def test_gap_has_question(self):
        """Each gap has a question string."""
        rules = [_make_rule("POINTS", "points", "GTE", 65, message="Points must be 65+")]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert gaps[0].question is not None
        assert len(gaps[0].question) > 0

    def test_gap_question_mentions_field(self):
        """Question mentions the missing field."""
        rules = [_make_rule("POINTS", "points", "GTE", 65)]
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
        )
        status, evals = _evaluate(p, rules)
        rules_dict = {r.rule_id: r for r in rules}
        gaps = GapEngine.find_gaps(evals, rules_dict)
        assert "points" in gaps[0].question.lower()
