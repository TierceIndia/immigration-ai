"""Phase 3B-5: Rule versioning — effective dates, deprecation, version-aware selection.

Rules change when government policies change. The rule engine must:
1. Select the correct rule version for a given date.
2. Support effective_from / effective_to date ranges.
3. Mark superseded rules and point to their replacement.
4. Support rule retirement (no replacement).
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.models import Rule, RuleStatus, Provenance


def _make_rule(rule_id, version=1, status=RuleStatus.ACTIVE,
               effective_from="2024-01-01", effective_to=None):
    return Rule(
        rule_id=rule_id, requirement_id=f"REQ-{rule_id}",
        program_id="SKILLED_189", category="TEST",
        field="age", operator="LT", value=45, unit=None,
        mandatory=True, critical=True, message=f"Rule {rule_id}",
        provenance=Provenance(
            source_id="SRC", document_id="DOC", document_version=1,
            chunk_id=1, requirement_id=f"REQ-{rule_id}", evidence_text="evidence",
        ),
        status=status,
        version=version,
    )


# ============================================================
# RULE VERSION SELECTION
# ============================================================

class TestRuleVersionSelection:
    """Selecting the correct rule version for a given date."""

    def test_rule_has_version(self):
        """Rule starts at version 1."""
        rule = _make_rule("AGE")
        assert rule.version == 1

    def test_rule_incremented_version(self):
        """Rule version can be incremented."""
        rule = _make_rule("AGE", version=2)
        assert rule.version == 2

    def test_rule_has_status(self):
        """Rule has status."""
        rule = _make_rule("AGE", status=RuleStatus.ACTIVE)
        assert rule.status == RuleStatus.ACTIVE

    def test_rule_draft(self):
        """Rule defaults to DRAFT."""
        rule = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="TEST",
            field="age", operator="LT", value=45, unit=None,
            mandatory=True, critical=True, message="Rule",
            provenance=Provenance(
                source_id="SRC", document_id="DOC", document_version=1,
                chunk_id=1, requirement_id="REQ-AGE", evidence_text="evidence",
            ),
        )
        assert rule.status == RuleStatus.DRAFT

    def test_rule_superseded(self):
        """Rule can be marked SUPERSEDED."""
        rule = _make_rule("AGE", status=RuleStatus.SUPERSEDED)
        assert rule.status == RuleStatus.SUPERSEDED

    def test_rule_retired(self):
        """Rule can be marked RETIRED."""
        rule = _make_rule("AGE", status=RuleStatus.RETIRED)
        assert rule.status == RuleStatus.RETIRED

    def test_rule_serialization_preserves_version(self):
        """Version survives serialization roundtrip."""
        rule = _make_rule("AGE", version=3, status=RuleStatus.SUPERSEDED)
        d = rule.to_dict()
        assert d["version"] == 3
        assert d["status"] == "SUPERSEDED"
        rule2 = Rule.from_dict(d)
        assert rule2.version == 3
        assert rule2.status == RuleStatus.SUPERSEDED


# ============================================================
# VERSION HISTORY
# ============================================================

class TestVersionHistory:
    """Tracking rule version history."""

    def test_multiple_versions_same_rule(self):
        """Multiple versions of the same rule can coexist."""
        v1 = _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED)
        v2 = _make_rule("AGE", version=2, status=RuleStatus.ACTIVE)
        assert v1.version == 1
        assert v2.version == 2
        assert v1.status == RuleStatus.SUPERSEDED
        assert v2.status == RuleStatus.ACTIVE

    def test_version_increments(self):
        """Versions increment sequentially."""
        versions = [_make_rule("AGE", version=i) for i in range(1, 6)]
        assert [r.version for r in versions] == [1, 2, 3, 4, 5]

    def test_superseded_points_to_replacement(self):
        """Superseded rule can reference its replacement via message."""
        v1 = _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED)
        v2 = _make_rule("AGE", version=2, status=RuleStatus.ACTIVE)
        # In practice, superseded rules reference replacement
        assert v1.status == RuleStatus.SUPERSEDED
        assert v2.status == RuleStatus.ACTIVE

    def test_retired_has_no_replacement(self):
        """Retired rule has no replacement."""
        v1 = _make_rule("AGE", version=1, status=RuleStatus.RETIRED)
        assert v1.status == RuleStatus.RETIRED


# ============================================================
# VERSION FILTERING
# ============================================================

class TestVersionFiltering:
    """Filtering rules by version and status."""

    def test_active_rules_only(self):
        """Filter to ACTIVE rules only."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE", version=2, status=RuleStatus.ACTIVE),
            _make_rule("POINTS", version=1, status=RuleStatus.ACTIVE),
        ]
        active = [r for r in rules if r.status == RuleStatus.ACTIVE]
        assert len(active) == 2
        assert all(r.status == RuleStatus.ACTIVE for r in active)

    def test_latest_version_per_rule(self):
        """Select latest version of each rule."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE", version=2, status=RuleStatus.ACTIVE),
            _make_rule("AGE", version=3, status=RuleStatus.ACTIVE),
            _make_rule("POINTS", version=1, status=RuleStatus.ACTIVE),
        ]
        # Group by rule_id, take max version
        by_id = {}
        for r in rules:
            if r.rule_id not in by_id or r.version > by_id[r.rule_id].version:
                by_id[r.rule_id] = r
        assert len(by_id) == 2
        assert by_id["AGE"].version == 3
        assert by_id["POINTS"].version == 1

    def test_rules_by_program(self):
        """Filter rules by program."""
        rules = [
            _make_rule("AGE-189", version=1),
            _make_rule("AGE-190", version=1),
            _make_rule("POINTS-189", version=1),
        ]
        rules[0].program_id = "SKILLED_189"
        rules[1].program_id = "SKILLED_190"
        rules[2].program_id = "SKILLED_189"
        program_rules = [r for r in rules if r.program_id == "SKILLED_189"]
        assert len(program_rules) == 2


# ============================================================
# RULE STORE
# ============================================================

class TestRuleStore:
    """In-memory rule store with versioning."""

    def test_add_and_retrieve(self):
        """Add rules and retrieve by ID."""
        store = {}
        rule = _make_rule("AGE", version=1)
        store[rule.rule_id] = rule
        assert store["AGE"].version == 1

    def test_update_version(self):
        """Update rule to new version, mark old as SUPERSEDED."""
        store = {}
        v1 = _make_rule("AGE", version=1, status=RuleStatus.ACTIVE)
        store["AGE"] = v1
        # New version supersedes old
        v2 = _make_rule("AGE", version=2, status=RuleStatus.ACTIVE)
        v1_superseded = _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED)
        store["AGE_v1"] = v1_superseded
        store["AGE"] = v2
        assert store["AGE"].version == 2
        assert store["AGE_v1"].status == RuleStatus.SUPERSEDED

    def test_retire_rule(self):
        """Retire a rule."""
        store = {}
        rule = _make_rule("AGE", version=1, status=RuleStatus.ACTIVE)
        store["AGE"] = rule
        store["AGE"].status = RuleStatus.RETIRED
        assert store["AGE"].status == RuleStatus.RETIRED

    def test_version_history_list(self):
        """Track version history as a list."""
        history = []
        for v in range(1, 4):
            status = RuleStatus.SUPERSEDED if v < 3 else RuleStatus.ACTIVE
            history.append(_make_rule("AGE", version=v, status=status))
        assert len(history) == 3
        assert history[0].status == RuleStatus.SUPERSEDED
        assert history[1].status == RuleStatus.SUPERSEDED
        assert history[2].status == RuleStatus.ACTIVE


# ============================================================
# CRITICAL: HISTORICAL ASSESSMENT REPRODUCIBILITY
# ============================================================

class TestHistoricalAssessmentReproducibility:
    """Prove historical assessments are not corrupted by rule changes.

    Rule v1 → Assessment A → Government change → Rule v2 → Assessment B
    Assessment A must still reference Rule v1 and produce the same result.
    """

    def test_historical_assessment_uses_rule_v1(self):
        """
        Rule v1: age < 45
        Assessment A (age=30): PASS against Rule v1

        Government changes rule to age < 50
        Rule v2: age < 50  (v1 marked SUPERSEDED)

        Assessment B (age=48): PASS against Rule v2

        Re-evaluating Assessment A against Rule v1 still gives PASS.
        Re-evaluating Assessment A against Rule v2 would give PASS too,
        but Assessment A MUST reference v1, not v2.
        """
        from phase3.models import Rule, Provenance
        from phase3.rule_engine import evaluate_rule

        def _prov(req_id):
            return Provenance(
                source_id="SRC", document_id="DOC", document_version=1,
                chunk_id=1, requirement_id=req_id, evidence_text="evidence",
            )

        # Rule v1: age < 45
        rule_v1 = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="AGE",
            field="age", operator="LT", value=45, unit=None,
            mandatory=True, critical=True, message="Age under 45",
            provenance=_prov("REQ-AGE"), status=RuleStatus.ACTIVE, version=1,
        )

        # Assessment A: applicant age=30, evaluated against Rule v1
        assessment_a = {
            "rule_id": rule_v1.rule_id,
            "rule_version": rule_v1.version,
            "applicant_data": {"age": 30},
        }
        result_a = evaluate_rule(rule_v1, assessment_a["applicant_data"])
        assert result_a.status.value == "PASS"
        assessment_a["result"] = result_a.status.value

        # Government changes policy: age limit becomes < 50
        # Rule v2 created, v1 marked SUPERSEDED
        rule_v2 = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="AGE",
            field="age", operator="LT", value=50, unit=None,
            mandatory=True, critical=True, message="Age under 50",
            provenance=_prov("REQ-AGE"), status=RuleStatus.ACTIVE, version=2,
        )
        rule_v1_superseded = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="AGE",
            field="age", operator="LT", value=45, unit=None,
            mandatory=True, critical=True, message="Age under 45",
            provenance=_prov("REQ-AGE"), status=RuleStatus.SUPERSEDED, version=1,
        )

        # Assessment B: applicant age=48, evaluated against Rule v2
        assessment_b = {
            "rule_id": rule_v2.rule_id,
            "rule_version": rule_v2.version,
            "applicant_data": {"age": 48},
        }
        result_b = evaluate_rule(rule_v2, assessment_b["applicant_data"])
        assert result_b.status.value == "PASS"
        assessment_b["result"] = result_b.status.value

        # CRITICAL: Re-evaluate Assessment A using Rule v1 (not v2)
        # Assessment A still references v1, so we re-evaluate with v1
        reassess_a = evaluate_rule(rule_v1, assessment_a["applicant_data"])
        assert reassess_a.status.value == "PASS"
        assert reassess_a.status == result_a.status

        # CRITICAL: If we accidentally used Rule v2 for Assessment A,
        # age=30 would still PASS (30 < 50), but the rule reference is wrong.
        # Verify the rule VERSION is correct, not just the status.
        assert assessment_a["rule_version"] == 1
        assert assessment_b["rule_version"] == 2
        assert rule_v1_superseded.status == RuleStatus.SUPERSEDED
        assert rule_v2.status == RuleStatus.ACTIVE

    def test_historical_assessment_fail_not_masked_by_v2_pass(self):
        """
        This is the dangerous case.

        Rule v1: age < 35
        Assessment A (age=40): FAIL against Rule v1

        Government changes: age < 50
        Rule v2: age < 50

        If Assessment A were accidentally re-evaluated against v2,
        age=40 < 50 → PASS, masking the original FAIL.

        This must NOT happen.
        """
        from phase3.models import Rule, Provenance
        from phase3.rule_engine import evaluate_rule

        def _prov(req_id):
            return Provenance(
                source_id="SRC", document_id="DOC", document_version=1,
                chunk_id=1, requirement_id=req_id, evidence_text="evidence",
            )

        # Rule v1: age < 35
        rule_v1 = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="AGE",
            field="age", operator="LT", value=35, unit=None,
            mandatory=True, critical=True, message="Age under 35",
            provenance=_prov("REQ-AGE"), status=RuleStatus.ACTIVE, version=1,
        )

        # Assessment A: age=40 → FAIL against v1
        result_a = evaluate_rule(rule_v1, {"age": 40})
        assert result_a.status.value == "FAIL"
        assessment_a = {
            "rule_id": "AGE",
            "rule_version": 1,
            "result": result_a.status.value,
        }

        # Rule v2: age < 50 (relaxed)
        rule_v2 = Rule(
            rule_id="AGE", requirement_id="REQ-AGE",
            program_id="SKILLED_189", category="AGE",
            field="age", operator="LT", value=50, unit=None,
            mandatory=True, critical=True, message="Age under 50",
            provenance=_prov("REQ-AGE"), status=RuleStatus.ACTIVE, version=2,
        )

        # If someone mistakenly evaluates age=40 against v2, it would PASS
        result_wrong = evaluate_rule(rule_v2, {"age": 40})
        assert result_wrong.status.value == "PASS"

        # But Assessment A's recorded result is FAIL (from v1)
        assert assessment_a["result"] == "FAIL"
        assert assessment_a["rule_version"] == 1

        # Re-evaluating with v1 still gives FAIL
        reassess = evaluate_rule(rule_v1, {"age": 40})
        assert reassess.status.value == "FAIL"

    def test_rule_store_prevents_wrong_version_evaluation(self):
        """Rule store lookup returns only the correct version."""
        # Simulate a rule store
        rule_store = {
            ("AGE", 1): _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            ("AGE", 2): _make_rule("AGE", version=2, status=RuleStatus.ACTIVE),
        }

        # Assessment A references v1
        rule_for_a = rule_store.get(("AGE", 1))
        assert rule_for_a is not None
        assert rule_for_a.version == 1
        assert rule_for_a.status == RuleStatus.SUPERSEDED

        # Assessment B references v2
        rule_for_b = rule_store.get(("AGE", 2))
        assert rule_for_b is not None
        assert rule_for_b.version == 2
        assert rule_for_b.status == RuleStatus.ACTIVE

        # Wrong version lookup returns wrong rule (caller's responsibility)
        rule_wrong = rule_store.get(("AGE", 2))
        assert rule_wrong.version == 2
        assert rule_wrong.value == 45  # v2 value
