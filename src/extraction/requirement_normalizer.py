"""Normalization layer.

Normalizes operator/unit/category tokens to the controlled vocabularies in
requirements.schema.json. Deliberately conservative: if a value doesn't map cleanly onto the
controlled set, it is left as UNKNOWN/None and flagged for review rather than silently coerced -
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

    return NormalizationResult(normalized=out, changes=changes, warnings=warnings)
