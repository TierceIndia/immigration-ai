"""Phase 3C-2: Rule Applicability — determine which rules apply to an applicant.

Before evaluating rules, we must determine:
1. Which rules match this visa program?
2. Which rules are currently ACTIVE (not SUPERSEDED/RETIRED)?
3. Which rule version is correct?
4. Do dependencies resolve?
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.models import Rule, RuleStatus, Provenance
from phase3c.applicant import ApplicantProfile, VisaSelection
from phase3c.applicability import filter_applicable_rules, select_rule_version


def _make_rule(rule_id, program_id="SKILLED_189", status=RuleStatus.ACTIVE,
               version=1, category="AGE", field="age", operator="LT", value=45):
    return Rule(
        rule_id=rule_id, requirement_id=f"REQ-{rule_id}",
        program_id=program_id, category=category,
        field=field, operator=operator, value=value, unit=None,
        mandatory=True, critical=True, message=f"Rule {rule_id}",
        provenance=Provenance(
            source_id="SRC", document_id="DOC", document_version=1,
            chunk_id=1, requirement_id=f"REQ-{rule_id}", evidence_text="evidence",
        ),
        status=status, version=version,
    )


# ============================================================
# PROGRAM FILTERING
# ============================================================

class TestProgramFiltering:
    """Filter rules by visa program."""

    def test_matching_program(self):
        """Rule for 189 applies to 189 applicant."""
        rules = [_make_rule("AGE", program_id="SKILLED_189")]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 1

    def test_non_matching_program(self):
        """Rule for 190 does NOT apply to 189 applicant."""
        rules = [_make_rule("NOMINATION", program_id="SKILLED_190")]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0

    def test_multiple_programs(self):
        """Mixed 189 and 190 rules filtered correctly."""
        rules = [
            _make_rule("AGE-189", program_id="SKILLED_189"),
            _make_rule("AGE-190", program_id="SKILLED_190"),
            _make_rule("POINTS-189", program_id="SKILLED_189"),
        ]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 2
        ids = {r.rule_id for r in result}
        assert "AGE-189" in ids
        assert "POINTS-189" in ids


# ============================================================
# STATUS FILTERING
# ============================================================

class TestStatusFiltering:
    """Filter rules by status — only ACTIVE rules apply."""

    def test_active_rule_applies(self):
        """ACTIVE rule applies."""
        rules = [_make_rule("AGE", status=RuleStatus.ACTIVE)]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 1

    def test_superseded_rule_excluded(self):
        """SUPERSEDED rule does not apply."""
        rules = [_make_rule("AGE", status=RuleStatus.SUPERSEDED)]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0

    def test_retired_rule_excluded(self):
        """RETIRED rule does not apply."""
        rules = [_make_rule("AGE", status=RuleStatus.RETIRED)]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0

    def test_draft_rule_excluded(self):
        """DRAFT rule does not apply."""
        rules = [_make_rule("AGE", status=RuleStatus.DRAFT)]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0

    def test_mixed_statuses(self):
        """Only ACTIVE rules survive."""
        rules = [
            _make_rule("AGE", status=RuleStatus.ACTIVE),
            _make_rule("POINTS", status=RuleStatus.SUPERSEDED),
            _make_rule("ENGLISH", status=RuleStatus.ACTIVE),
            _make_rule("SKILLS", status=RuleStatus.RETIRED),
        ]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 2
        ids = {r.rule_id for r in result}
        assert ids == {"AGE", "ENGLISH"}


# ============================================================
# VERSION SELECTION
# ============================================================

class TestVersionSelection:
    """Select the correct rule version when multiple versions exist."""

    def test_selects_latest_active(self):
        """When multiple versions exist, select the latest ACTIVE one."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE", version=2, status=RuleStatus.ACTIVE),
        ]
        result = select_rule_version(rules, "AGE")
        assert result is not None
        assert result.version == 2

    def test_single_version(self):
        """Single version returned as-is."""
        rules = [_make_rule("AGE", version=1, status=RuleStatus.ACTIVE)]
        result = select_rule_version(rules, "AGE")
        assert result.version == 1

    def test_no_matching_rule(self):
        """Non-existent rule returns None."""
        rules = [_make_rule("AGE", version=1, status=RuleStatus.ACTIVE)]
        result = select_rule_version(rules, "POINTS")
        assert result is None

    def test_all_superseded_returns_none(self):
        """If all versions are superseded, return None."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE", version=2, status=RuleStatus.SUPERSEDED),
        ]
        result = select_rule_version(rules, "AGE")
        assert result is None

    def test_highest_version_wins(self):
        """Among ACTIVE versions, highest version wins."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.ACTIVE),
            _make_rule("AGE", version=3, status=RuleStatus.ACTIVE),
            _make_rule("AGE", version=2, status=RuleStatus.ACTIVE),
        ]
        result = select_rule_version(rules, "AGE")
        assert result.version == 3


# ============================================================
# COMBINED FILTERING
# ============================================================

class TestCombinedFiltering:
    """Program + status + version filtering together."""

    def test_full_pipeline(self):
        """Filter by program, status, select version."""
        rules = [
            _make_rule("AGE-189-v1", program_id="SKILLED_189", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE-189-v2", program_id="SKILLED_189", version=2, status=RuleStatus.ACTIVE),
            _make_rule("AGE-190", program_id="SKILLED_190", version=1, status=RuleStatus.ACTIVE),
            _make_rule("POINTS-189", program_id="SKILLED_189", version=1, status=RuleStatus.ACTIVE),
        ]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        applicable = filter_applicable_rules(rules, p)
        # Should get AGE-189-v2 and POINTS-189 (both ACTIVE + correct program)
        assert len(applicable) == 2
        age_rule = select_rule_version(applicable, "AGE-189-v2")
        assert age_rule is not None
        assert age_rule.version == 2

    def test_empty_result(self):
        """No rules match."""
        rules = [
            _make_rule("AGE-190", program_id="SKILLED_190"),
            _make_rule("POINTS-190", program_id="SKILLED_190"),
        ]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0

    def test_all_versions_superseded(self):
        """All versions superseded -> empty."""
        rules = [
            _make_rule("AGE", version=1, status=RuleStatus.SUPERSEDED),
            _make_rule("AGE", version=2, status=RuleStatus.SUPERSEDED),
        ]
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        result = filter_applicable_rules(rules, p)
        assert len(result) == 0
