"""Phase 3C-5: Deterministic Questions — field → question → validation → answer normalization.

Flow:
UNKNOWN rule → missing field → question definition → user question → answer → normalized value → re-evaluate

Questions are deterministic templates, not LLM-generated.
Answer validation ensures only accepted values pass through.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from assessment.questions import QuestionDefinition, QuestionBank, AnswerNormalizer


# ============================================================
# QUESTION DEFINITIONS
# ============================================================

class TestQuestionDefinitions:
    """Deterministic question definitions for each field."""

    def test_age_question(self):
        """Age question exists."""
        q = QuestionBank.get_question("age")
        assert q is not None
        assert "age" in q.text.lower()

    def test_english_question(self):
        """English level question exists."""
        q = QuestionBank.get_question("english_level")
        assert q is not None
        assert "english" in q.text.lower()

    def test_points_question(self):
        """Points question exists."""
        q = QuestionBank.get_question("points")
        assert q is not None
        assert "points" in q.text.lower()

    def test_skills_assessment_question(self):
        """Skills assessment question exists."""
        q = QuestionBank.get_question("skills_assessment_status")
        assert q is not None
        assert "skills" in q.text.lower()

    def test_health_question(self):
        """Health question exists."""
        q = QuestionBank.get_question("health_status")
        assert q is not None

    def test_character_question(self):
        """Character question exists."""
        q = QuestionBank.get_question("character_status")
        assert q is not None

    def test_occupation_question(self):
        """Occupation question exists."""
        q = QuestionBank.get_question("occupation")
        assert q is not None


# ============================================================
# QUESTION VALIDATION
# ============================================================

class TestQuestionValidation:
    """Questions validate answer types."""

    def test_age_accepts_integer(self):
        """Age accepts integer."""
        q = QuestionBank.get_question("age")
        assert q.validate(30) is True

    def test_age_rejects_string(self):
        """Age rejects string."""
        q = QuestionBank.get_question("age")
        assert q.validate("thirty") is False

    def test_age_rejects_negative(self):
        """Age rejects negative."""
        q = QuestionBank.get_question("age")
        assert q.validate(-1) is False

    def test_age_rejects_over_120(self):
        """Age rejects > 120."""
        q = QuestionBank.get_question("age")
        assert q.validate(130) is False

    def test_english_accepts_valid_levels(self):
        """English accepts valid levels."""
        q = QuestionBank.get_question("english_level")
        for level in ["none", "vocational", "competent", "proficient", "superior"]:
            assert q.validate(level) is True

    def test_english_rejects_invalid_level(self):
        """English rejects invalid level."""
        q = QuestionBank.get_question("english_level")
        assert q.validate("advanced") is False

    def test_points_accepts_integer(self):
        """Points accepts integer."""
        q = QuestionBank.get_question("points")
        assert q.validate(80) is True

    def test_points_rejects_string(self):
        """Points rejects string."""
        q = QuestionBank.get_question("points")
        assert q.validate("high") is False

    def test_points_rejects_negative(self):
        """Points rejects negative."""
        q = QuestionBank.get_question("points")
        assert q.validate(-5) is False

    def test_skills_accepts_valid_statuses(self):
        """Skills accepts valid statuses."""
        q = QuestionBank.get_question("skills_assessment_status")
        for s in ["NOT_APPLICABLE", "PENDING", "VALID", "INVALID", "EXEMPT"]:
            assert q.validate(s) is True

    def test_skills_rejects_invalid(self):
        """Skills rejects invalid status."""
        q = QuestionBank.get_question("skills_assessment_status")
        assert q.validate("PASSED") is False


# ============================================================
# ANSWER NORMALIZATION
# ============================================================

class TestAnswerNormalization:
    """Normalize user answers to canonical values."""

    def test_age_normalizes_string_to_int(self):
        """Age '30' → 30."""
        result = AnswerNormalizer.normalize("age", "30")
        assert result == 30
        assert isinstance(result, int)

    def test_age_normalizes_int(self):
        """Age 30 → 30."""
        result = AnswerNormalizer.normalize("age", 30)
        assert result == 30

    def test_english_normalizes_case(self):
        """English 'PROFICIENT' → 'proficient'."""
        result = AnswerNormalizer.normalize("english_level", "PROFICIENT")
        assert result == "proficient"

    def test_english_normalizes_alias(self):
        """English 'IELTS 7' → 'competent'."""
        result = AnswerNormalizer.normalize("english_level", "IELTS 7")
        assert result == "competent"

    def test_points_normalizes_string_to_int(self):
        """Points '80' → 80."""
        result = AnswerNormalizer.normalize("points", "80")
        assert result == 80

    def test_skills_normalizes_case(self):
        """Skills 'valid' → 'VALID'."""
        result = AnswerNormalizer.normalize("skills_assessment_status", "valid")
        assert result == "VALID"

    def test_health_normalizes_case(self):
        """Health 'passed' → 'PASSED'."""
        result = AnswerNormalizer.normalize("health_status", "passed")
        assert result == "PASSED"


# ============================================================
# QUESTION BANK COMPLETENESS
# ============================================================

class TestQuestionBankCompleteness:
    """Question bank covers all user-facing fields."""

    def test_all_gappable_fields_have_questions(self):
        """Every field that can be UNKNOWN has a question."""
        gappable_fields = [
            "age", "english_level", "points", "occupation",
            "skills_assessment_status", "health_status", "character_status",
            "has_regional_nomination", "visa_subclass_detail",
        ]
        for field in gappable_fields:
            q = QuestionBank.get_question(field)
            assert q is not None, f"Missing question for {field}"

    def test_question_id_matches_field(self):
        """Question ID matches the field name."""
        q = QuestionBank.get_question("age")
        assert q.field == "age"
