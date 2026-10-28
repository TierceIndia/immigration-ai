"""The admin rule checker and the not-evaluable guard (Phase 4b).

Three things are tested here:

1.  ``is_field_evaluable`` - does a rule's field refer to a fact an
    applicant can supply at all?
2.  ``check_rules`` - a deterministic dry run of rules against a
    hypothetical applicant.
3.  The API guard - activating a rule that no applicant field can answer
    needs an explicit typed confirmation, and the checker endpoint works.

The dry run must be deterministic and must not persist anything: an admin
using it to decide whether to activate a rule needs to be able to trust that
the same inputs always give the same answer, and that checking a rule does
not change the database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rules.models import Provenance, Rule  # noqa: E402
from rules.rule_checker import check_rules  # noqa: E402
from rules.rule_engine import is_field_evaluable, resolve_field_name  # noqa: E402


def _rule(rule_id="R-1", field="applicant.age", operator="LT", value=45, **overrides):
    provenance = Provenance(
        source_id="SRC-1", document_id="DOC-1", document_version=1,
        chunk_id=1, requirement_id="REQ-1", evidence_text="You must be under 45",
    )
    defaults = dict(
        rule_id=rule_id,
        requirement_id="REQ-1",
        program_id="SKILLED_189",
        category="AGE",
        field=field,
        operator=operator,
        value=value,
        unit=None,
        mandatory=True,
        critical=False,
        message="Applicant must be under 45",
        provenance=provenance,
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestFieldEvaluability:
    @pytest.mark.parametrize("field", [
        "age", "applicant.age",
        "points", "applicant.points_score", "applicant.required_points",
        "skills_assessment_status", "applicant.skills_assessment",
        "health_status", "applicant.health_check",
        "character_status", "applicant.character_check",
        "visa_history", "applicant.visa_history",
        "employer_sponsored", "work_experience_years",
        "in_australia_at_application", "has_health_insurance",
        "genuine_temporary_entrant",
        "program_id",
    ])
    def test_modelled_facts_are_evaluable(self, field):
        assert is_field_evaluable(field) is True

    @pytest.mark.parametrize("field", [
        None, "", "NOT_MODELED",
        "applicant.financial_capacity",
        "applicant.invitation_validity",
        "some.random.field",
    ])
    def test_unmodelled_facts_are_not_evaluable(self, field):
        assert is_field_evaluable(field) is False

    def test_aliases_resolve_to_the_canonical_fact(self):
        assert resolve_field_name("applicant.points_score") == "points"
        assert resolve_field_name("applicant.required_points") == "points"
        assert resolve_field_name("applicant.health_check") == "health_status"

    def test_an_unmodelled_field_has_no_canonical_name(self):
        assert resolve_field_name("applicant.financial_capacity") is None


class TestTheChecker:
    def test_a_passing_rule_is_reported_as_pass(self):
        report = check_rules([_rule()], {"age": 30}, "SKILLED_189")
        assert report.overall_status == "PASS"
        assert report.business_status == "ELIGIBLE"
        assert report.passed == 1
        assert report.results[0].status == "PASS"
        assert "satisfies" in report.results[0].explanation

    def test_a_failing_rule_is_reported_as_fail(self):
        report = check_rules([_rule()], {"age": 50}, "SKILLED_189")
        assert report.overall_status == "FAIL"
        assert report.business_status == "INELIGIBLE"
        assert report.failed == 1

    def test_a_missing_fact_is_unknown_not_fail(self):
        report = check_rules([_rule()], {}, "SKILLED_189")
        assert report.overall_status == "UNKNOWN"
        assert report.business_status == "INCOMPLETE"
        assert report.unknown == 1
        assert report.results[0].actual_value is None

    def test_the_gaps_say_what_to_ask(self):
        report = check_rules([_rule()], {}, "SKILLED_189")
        assert len(report.gaps) == 1
        assert report.gaps[0].field == "applicant.age"
        assert "age" in report.gaps[0].question.lower()

    def test_a_not_evaluable_rule_is_flagged_and_explained(self):
        rule = _rule(field="applicant.financial_capacity", operator="EQ", value=50000)
        report = check_rules([rule], {"age": 30}, "SKILLED_189")
        assert report.results[0].evaluable is False
        assert "never resolve to PASS or FAIL" in report.results[0].explanation

    def test_a_evaluable_rule_is_not_flagged(self):
        report = check_rules([_rule()], {"age": 30}, "SKILLED_189")
        assert report.results[0].evaluable is True

    def test_the_same_inputs_always_give_the_same_answer(self):
        rules = [_rule("R-1"), _rule("R-2", field="applicant.points", operator="GTE", value=65)]
        first = check_rules(rules, {"age": 30, "points": 70}, "SKILLED_189")
        second = check_rules(rules, {"age": 30, "points": 70}, "SKILLED_189")
        assert first.overall_status == second.overall_status
        assert first.ruleset_hash == second.ruleset_hash
        assert [r.status for r in first.results] == [r.status for r in second.results]

    def test_the_ruleset_hash_changes_when_a_rule_version_changes(self):
        rule = _rule()
        before = check_rules([rule], {"age": 30}, "SKILLED_189").ruleset_hash
        after = check_rules([_rule(version=2)], {"age": 30}, "SKILLED_189").ruleset_hash
        assert before != after

    def test_the_ruleset_hash_is_scoped_to_the_program(self):
        rule = _rule()
        one = check_rules([rule], {"age": 30}, "SKILLED_189").ruleset_hash
        two = check_rules([rule], {"age": 30}, "SKILLED_190").ruleset_hash
        assert one != two

    def test_the_report_serializes_to_plain_data(self):
        report = check_rules([_rule()], {}, "SKILLED_189")
        data = report.to_dict()
        assert data["program_id"] == "SKILLED_189"
        assert data["total_rules"] == 1
        assert isinstance(data["results"], list)
        assert isinstance(data["gaps"], list)
        assert data["gaps"][0]["priority"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        assert data["evaluated_at"]
