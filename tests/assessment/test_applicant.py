"""Phase 3C-1: Applicant Profile data model.

Canonical applicant profile for eligibility assessment.

Key principle: missing fields remain None/null, NOT converted to FAIL.
The rule engine distinguishes "field missing" (UNKNOWN) from "field present and failing" (FAIL).
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from assessment.applicant import ApplicantProfile, VisaSelection


# ============================================================
# BASIC CONSTRUCTION
# ============================================================

class TestApplicantProfileConstruction:
    """Building applicant profiles from raw data."""

    def test_full_profile(self):
        """All fields present."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
            english_level="proficient",
            points=70,
            occupation="261313",
            skills_assessment_status="VALID",
            health_status="PASSED",
            character_status="PASSED",
        )
        assert p.age == 30
        assert p.visa.program_id == "SKILLED_189"

    def test_minimal_profile(self):
        """Only visa selection, everything else None."""
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"))
        assert p.age is None
        assert p.english_level is None
        assert p.points is None

    def test_missing_fields_are_none(self):
        """Missing fields are None, not 0, not empty string."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
        )
        assert p.english_level is None
        assert p.points is None
        assert p.occupation is None

    def test_visa_selection(self):
        """Visa selection is required."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_190"),
        )
        assert p.visa.program_id == "SKILLED_190"

    def test_from_dict(self):
        """Construct from dictionary."""
        d = {
            "visa": {"program_id": "SKILLED_189"},
            "age": 32,
            "english_level": "proficient",
            "points": 80,
        }
        p = ApplicantProfile.from_dict(d)
        assert p.age == 32
        assert p.visa.program_id == "SKILLED_189"
        assert p.occupation is None

    def test_from_dict_empty(self):
        """Construct from empty dict — visa required."""
        import pytest
        with pytest.raises(ValueError):
            ApplicantProfile.from_dict({})

    def test_to_dict_roundtrip(self):
        """Serialization roundtrip preserves None fields."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
            english_level="proficient",
        )
        d = p.to_dict()
        p2 = ApplicantProfile.from_dict(d)
        assert p2.age == 30
        assert p2.english_level == "proficient"
        assert p2.points is None


# ============================================================
# FIELD TYPES
# ============================================================

class TestFieldTypes:
    """Field types and allowed values."""

    def test_age_integer(self):
        """Age must be integer."""
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), age=30)
        assert isinstance(p.age, int)

    def test_age_none(self):
        """Age can be None."""
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"))
        assert p.age is None

    def test_english_level_string(self):
        """English level is a string from controlled vocabulary."""
        for level in ["none", "vocational", "competent", "proficient", "superior"]:
            p = ApplicantProfile(
                visa=VisaSelection(program_id="SKILLED_189"),
                english_level=level,
            )
            assert p.english_level == level

    def test_points_integer(self):
        """Points must be integer."""
        p = ApplicantProfile(visa=VisaSelection(program_id="SKILLED_189"), points=80)
        assert isinstance(p.points, int)

    def test_skills_assessment_status(self):
        """Skills assessment status from controlled vocabulary."""
        for status in ["NOT_APPLICABLE", "PENDING", "VALID", "INVALID", "EXEMPT"]:
            p = ApplicantProfile(
                visa=VisaSelection(program_id="SKILLED_189"),
                skills_assessment_status=status,
            )
            assert p.skills_assessment_status == status

    def test_health_status(self):
        """Health status from controlled vocabulary."""
        for status in ["UNKNOWN", "PASSED", "FAILED"]:
            p = ApplicantProfile(
                visa=VisaSelection(program_id="SKILLED_189"),
                health_status=status,
            )
            assert p.health_status == status

    def test_character_status(self):
        """Character status from controlled vocabulary."""
        for status in ["UNKNOWN", "PASSED", "FAILED"]:
            p = ApplicantProfile(
                visa=VisaSelection(program_id="SKILLED_189"),
                character_status=status,
            )
            assert p.character_status == status


# ============================================================
# TO RULE DATA
# ============================================================

class TestProfileToRuleData:
    """Convert profile to dict for rule engine evaluation."""

    def test_to_rule_data_full(self):
        """Full profile converts to flat dict."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
            english_level="proficient",
            points=70,
            occupation="261313",
            skills_assessment_status="VALID",
            health_status="PASSED",
            character_status="PASSED",
        )
        d = p.to_rule_data()
        assert d["age"] == 30
        assert d["english_level"] == "proficient"
        assert d["points"] == 70
        assert d["skills_assessment_status"] == "VALID"
        assert d["program_id"] == "SKILLED_189"

    def test_to_rule_data_missing_fields_absent(self):
        """Missing fields are absent from rule data, not None."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
        )
        d = p.to_rule_data()
        assert "english_level" not in d
        assert "points" not in d
        assert "skills_assessment_status" not in d
        assert d["age"] == 30

    def test_to_rule_data_preserves_none_for_present_fields(self):
        """Explicitly-set None fields are absent."""
        p = ApplicantProfile(
            visa=VisaSelection(program_id="SKILLED_189"),
            age=30,
            english_level=None,
        )
        d = p.to_rule_data()
        assert "english_level" not in d


# ============================================================
# VISA SELECTION
# ============================================================

class TestVisaSelection:
    """Visa/program selection."""

    def test_189(self):
        """Select 189."""
        v = VisaSelection(program_id="SKILLED_189")
        assert v.program_id == "SKILLED_189"

    def test_190(self):
        """Select 190."""
        v = VisaSelection(program_id="SKILLED_190")
        assert v.program_id == "SKILLED_190"

    def test_491(self):
        """Select 491."""
        v = VisaSelection(program_id="SKILLED_491")
        assert v.program_id == "SKILLED_491"

    def test_to_dict(self):
        """VisaSelection serialization."""
        v = VisaSelection(program_id="SKILLED_189")
        d = v.to_dict()
        assert d["program_id"] == "SKILLED_189"

    def test_from_dict(self):
        """VisaSelection deserialization."""
        v = VisaSelection.from_dict({"program_id": "SKILLED_190"})
        assert v.program_id == "SKILLED_190"
