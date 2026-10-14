"""Normalization layer.

Normalizes operator/unit/category tokens to the controlled vocabularies in
requirements.schema.json. Deliberately conservative: an operator that doesn't map cleanly
defaults to UNKNOWN, a category that doesn't map cleanly defaults to OTHER, and a unit that
doesn't map cleanly is kept verbatim (not coerced). In every case the normalization is
reported back as a warning for human review rather than silently accepted -
"do not normalize away important legal meaning" (per Phase 2 spec).
"""
from __future__ import annotations

from dataclasses import dataclass

_OPERATOR_ALIASES = {
    "=": "EQ", "==": "EQ", "equals": "EQ",
    "!=": "NEQ", "not equal": "NEQ",
    "<": "LT", "less than": "LT", "under": "LT",
    "<=": "LTE", "at most": "LTE", "or younger": "LTE", "or under": "LTE",
    ">": "GT", "more than": "GT", "greater than": "GT",
    ">=": "GTE", "at least": "GTE", "minimum": "GTE",
    "in": "IN", "one of": "IN",
    "not in": "NOT_IN",
    "between": "BETWEEN",
    "required": "REQUIRED",
    "not required": "NOT_REQUIRED", "exempt": "NOT_REQUIRED",
}

_VALID_OPERATORS = {
    "EQ", "NEQ", "LT", "LTE", "GT", "GTE", "IN", "NOT_IN", "BETWEEN",
    "REQUIRED", "NOT_REQUIRED", "UNKNOWN",
}

_UNIT_ALIASES = {
    "year": "years", "yrs": "years", "yr": "years",
    "month": "months", "mos": "months",
    "week": "weeks", "wk": "weeks",
    "day": "days",
    "point": "points", "pts": "points",
    "aud": "AUD", "dollars": "AUD", "$": "AUD",
    "%": "percent",
}
_VALID_UNITS = {"years", "months", "weeks", "days", "points", "AUD", "percent"}

_VALID_CATEGORIES = {
    "AGE", "OCCUPATION", "SKILLS_ASSESSMENT", "EDUCATION", "WORK_EXPERIENCE",
    "ENGLISH_LANGUAGE", "POINTS", "INVITATION", "NOMINATION", "STATE_REQUIREMENT",
    "REGIONAL_REQUIREMENT", "HEALTH", "CHARACTER", "IDENTITY", "VISA_STATUS",
    "DOCUMENT", "FAMILY", "FINANCIAL", "APPLICATION", "OTHER",
    "DEBT", "VALUES_STATEMENT", "VISA_HISTORY",
}

# Closed field vocabulary (v3) - must match ApplicantProfile.to_rule_data()'s
# real output keys and rule_engine._FIELD_ALIASES exactly (with the
# "applicant." prefix the extraction prompt/schema uses), or a rule compiled
# from this requirement can never evaluate to PASS/FAIL, only UNKNOWN - see
# the live-validation finding on applicant.visa_history. NOT_MODELED is the
# explicit, honest alternative to inventing a new dotted name.
NOT_MODELED = "NOT_MODELED"
_EVALUABLE_FIELDS = {
    "applicant.age", "applicant.english_level", "applicant.points_score",
    "applicant.required_points", "applicant.occupation", "applicant.skills_assessment",
    "applicant.health_check", "applicant.character_check",
    "applicant.has_regional_nomination", "applicant.visa_subclass",
}


@dataclass
class NormalizationResult:
    normalized: dict
    changes: list[str]
    warnings: list[str]


def normalize_operator(op: str | None) -> tuple[str, str | None]:
    if op is None:
        return "UNKNOWN", "operator missing, defaulted to UNKNOWN"
    if op in _VALID_OPERATORS:
        return op, None
    alias = _OPERATOR_ALIASES.get(op.strip().lower())
    if alias:
        return alias, f"operator '{op}' normalized to '{alias}'"
    return "UNKNOWN", f"unrecognized operator '{op}', defaulted to UNKNOWN for review"


def normalize_unit(unit: str | None) -> tuple[str | None, str | None]:
    if unit is None:
        return None, None
    if unit in _VALID_UNITS:
        return unit, None
    alias = _UNIT_ALIASES.get(unit.strip().lower())
    if alias:
        return alias, f"unit '{unit}' normalized to '{alias}'"
    return unit, f"unit '{unit}' not in controlled vocabulary - kept as-is, flagged for review"


def normalize_category(category: str | None) -> tuple[str, str | None]:
    if category is None:
        return "OTHER", "category missing, defaulted to OTHER"
    up = category.strip().upper()
    if up in _VALID_CATEGORIES:
        return up, None
    return "OTHER", f"unrecognized category '{category}', defaulted to OTHER for review"


def normalize_field(
    field: str | None, content_kind: str | None, concept: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Enforce the closed field vocabulary. Returns ``(field, concept, note)``.

    Vocabulary only applies to REQUIREMENT rows - a FACT/INFORMATIONAL/
    PROCEDURE/EXCEPTION/CROSS_REFERENCE row isn't an executable eligibility
    gate, so its field (commonly null anyway) is left untouched.

    A REQUIREMENT whose field is already null, already NOT_MODELED, or
    already in the evaluable set passes through unchanged. Anything else -
    the schema should already block this for a compliant real LLM, but a
    mock or an older/non-compliant response may still send one - is demoted
    to NOT_MODELED with the original string preserved as ``concept`` (never
    dropped), and reported as a warning so the row routes to NEEDS_REVIEW
    rather than being silently accepted as if it were evaluable.
    """
    if content_kind != "REQUIREMENT" or field is None or field == NOT_MODELED or field in _EVALUABLE_FIELDS:
        return field, concept, None
    resolved_concept = concept or field
    return (
        NOT_MODELED, resolved_concept,
        f"field '{field}' is not in the evaluable vocabulary - demoted to NOT_MODELED "
        f"(concept={resolved_concept!r})",
    )


def normalize_requirement(requirement: dict) -> NormalizationResult:
    changes: list[str] = []
    warnings: list[str] = []
    out = dict(requirement)

    op, note = normalize_operator(out.get("operator"))
    out["operator"] = op
    if note:
        (changes if op != "UNKNOWN" else warnings).append(note)

    unit, note = normalize_unit(out.get("unit"))
    out["unit"] = unit
    if note:
        if unit in _VALID_UNITS:
            changes.append(note)
        else:
            warnings.append(note)

    cat, note = normalize_category(out.get("category"))
    out["category"] = cat
    if note:
        (changes if cat != "OTHER" else warnings).append(note)

    field, concept, note = normalize_field(out.get("field"), out.get("content_kind"), out.get("concept"))
    out["field"] = field
    out["concept"] = concept
    if note:
        warnings.append(note)

    return NormalizationResult(normalized=out, changes=changes, warnings=warnings)
