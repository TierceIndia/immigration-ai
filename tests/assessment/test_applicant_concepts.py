"""The first batch of newly modelled applicant concepts (Phase 4).

Six facts that previously had nowhere to live: visa_history,
employer_sponsored, work_experience_years, in_australia_at_application,
has_health_insurance and genuine_temporary_entrant. Before this, a rule
about any of them could only ever answer UNKNOWN, no matter what an
applicant supplied.

These tests pin the whole path: the profile carries them, the intake schema
declares them, the extraction vocabulary accepts them, and the rule engine
can actually resolve a rule that references one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from assessment.applicant import ApplicantProfile, VisaSelection  # noqa: E402
from assessment.gap_engine import GapEngine  # noqa: E402
from assessment.questions import AnswerNormalizer, QuestionBank  # noqa: E402
from extraction.requirement_normalizer import normalize_field  # noqa: E402
from rules.models import EvaluationStatus, Provenance, Rule  # noqa: E402
from rules.rule_engine import evaluate_rule  # noqa: E402


NEW_CONCEPTS = {
    "visa_history": "PRIOR_REFUSED",
    "employer_sponsored": True,
    "work_experience_years": 7,
    "in_australia_at_application": False,
    "has_health_insurance": True,
    "genuine_temporary_entrant": False,
}

# The dotted names the extraction vocabulary uses for each concept.
DOTTED = {f"applicant.{name}": name for name in NEW_CONCEPTS}


def _profile(**overrides) -> ApplicantProfile:
    values = dict(NEW_CONCEPTS)
    values.update(overrides)
    return ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), **values)


class TestTheProfileCarriesTheNewConcepts:
    def test_all_six_are_declared(self):
        profile = _profile()
        for name, value in NEW_CONCEPTS.items():
            assert getattr(profile, name) == value

    def test_they_round_trip_through_to_dict(self):
        profile = _profile()
        data = profile.to_dict()
        for name, value in NEW_CONCEPTS.items():
            assert data[name] == value
        rebuilt = ApplicantProfile.from_dict(data)
        assert rebuilt.to_dict() == data

    def test_they_reach_the_rule_engine(self):
        # to_rule_data() is what the rule engine actually reads, and it only
        # carries non-None values - that is what makes a missing fact
        # UNKNOWN rather than FAIL.
        data = _profile().to_rule_data()
        for name, value in NEW_CONCEPTS.items():
            assert data[name] == value

    def test_an_unsupplied_concept_stays_missing(self):
        profile = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"))
        data = profile.to_rule_data()
        for name in NEW_CONCEPTS:
            assert name not in data


class TestTheIntakeSchemaDeclaresTheNewConcepts:
    @pytest.fixture()
    def intake_schema(self):
        return json.loads(
            (ROOT / "schema" / "applicant_intake.schema.json").read_text(encoding="utf-8")
        )

    def test_every_new_concept_is_in_the_intake_schema(self, intake_schema):
        properties = intake_schema["properties"]
        for name in NEW_CONCEPTS:
            assert name in properties, name

    def test_the_intake_schema_covers_every_profile_field(self, intake_schema):
        # The intake schema and the profile are two descriptions of the same
        # thing; a field in one and not the other is a silent gap.
        properties = set(intake_schema["properties"]) - {"visa"}
        profile_fields = set(_profile().to_dict()) - {"visa"}
        assert properties == profile_fields

    def test_visa_history_has_a_closed_value_vocabulary(self, intake_schema):
        enum = intake_schema["properties"]["visa_history"]["enum"]
        assert "PRIOR_REFUSED" in enum
        assert "NOT_MODELED" not in enum


class TestTheExtractionVocabularyAcceptsTheNewConcepts:
    def test_each_new_concept_survives_normalization(self):
        for dotted in DOTTED:
            field, concept, note = normalize_field(dotted, "REQUIREMENT")
            assert field == dotted
            assert concept is None
            assert note is None

    def test_an_unmodelled_concept_is_still_demoted(self):
        field, _, note = normalize_field("applicant.financial_capacity", "REQUIREMENT")
        assert field == "NOT_MODELED"
        assert note is not None


class TestTheRuleEngineCanResolveTheNewConcepts:
    def _rule(self, dotted: str, operator: str, value) -> Rule:
        return Rule(
            rule_id="RULE-TEST",
            requirement_id="REQ-TEST",
            program_id="SKILLED_189",
            category="TEST",
            field=dotted,
            operator=operator,
            value=value,
            unit=None,
            mandatory=True,
            critical=False,
            message="",
            provenance=Provenance(
                source_id="SRC-TEST", document_id="DOC-TEST", document_version=1,
                chunk_id=1, requirement_id="REQ-TEST", evidence_text="",
            ),
        )

    def test_a_boolean_concept_resolves_to_pass(self):
        rule = self._rule("applicant.employer_sponsored", "EQ", True)
        evaluation = evaluate_rule(rule, _profile().to_rule_data())
        assert evaluation.status == EvaluationStatus.PASS

    def test_a_boolean_concept_resolves_to_fail(self):
        rule = self._rule("applicant.employer_sponsored", "EQ", False)
        evaluation = evaluate_rule(rule, _profile().to_rule_data())
        assert evaluation.status == EvaluationStatus.FAIL

    def test_a_numeric_concept_resolves_against_a_threshold(self):
        rule = self._rule("applicant.work_experience_years", "GTE", 5)
        evaluation = evaluate_rule(rule, _profile().to_rule_data())
        assert evaluation.status == EvaluationStatus.PASS

        stricter = self._rule("applicant.work_experience_years", "GTE", 10)
        evaluation = evaluate_rule(stricter, _profile().to_rule_data())
        assert evaluation.status == EvaluationStatus.FAIL

    def test_an_unsupplied_concept_is_unknown_not_fail(self):
        # The whole point of the rule engine's contract: a fact that was
        # never supplied is UNKNOWN, never a silent FAIL.
        rule = self._rule("applicant.has_health_insurance", "EQ", True)
        empty = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189")).to_rule_data()
        evaluation = evaluate_rule(rule, empty)
        assert evaluation.status == EvaluationStatus.UNKNOWN


class TestTheQuestionBankAsksTheNewConcepts:
    def test_each_new_concept_has_a_question(self):
        for name in NEW_CONCEPTS:
            assert QuestionBank.get_question(name) is not None, name

    def test_visa_history_only_accepts_the_closed_vocabulary(self):
        question = QuestionBank.get_question("visa_history")
        assert question.validate("PRIOR_REFUSED") is True
        assert question.validate("MADE_UP") is False

    def test_boolean_concepts_accept_true_and_false(self):
        for name in ("employer_sponsored", "in_australia_at_application",
                     "has_health_insurance", "genuine_temporary_entrant"):
            question = QuestionBank.get_question(name)
            assert question.validate(True) is True
            assert question.validate(False) is True
            assert question.validate("maybe") is False

    def test_answers_are_normalized_to_the_canonical_form(self):
        assert AnswerNormalizer.normalize("visa_history", "prior_refused") == "PRIOR_REFUSED"
        assert AnswerNormalizer.normalize("employer_sponsored", "yes") is True
        assert AnswerNormalizer.normalize("work_experience_years", "7") == 7


class TestTheGapEngineAsksTheNewConcepts:
    def test_a_missing_new_concept_produces_a_question_a_person_can_answer(self):
        rule = Rule(
            rule_id="RULE-GAP",
            requirement_id="REQ-GAP",
            program_id="SKILLED_189",
            category="TEST",
            field="applicant.genuine_temporary_entrant",
            operator="EQ",
            value=True,
            unit=None,
            mandatory=True,
            critical=False,
            message="",
            provenance=Provenance(
                source_id="SRC-TEST", document_id="DOC-TEST", document_version=1,
                chunk_id=1, requirement_id="REQ-TEST", evidence_text="",
            ),
        )
        evaluation = evaluate_rule(rule, {})
        gaps = GapEngine.find_gaps([evaluation], {"RULE-GAP": rule})
        assert len(gaps) == 1
        assert gaps[0].field == "applicant.genuine_temporary_entrant"
        assert "genuine temporary entrant" in gaps[0].question.lower()
