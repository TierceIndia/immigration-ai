"""Deterministic validation of LLM output. Two layers:

1. schema validation (requirements.schema.json via jsonschema) - structural correctness.
2. logic consistency - operator/value/unit coherence, provenance shape sanity.

Neither layer trusts the LLM. Both must pass before a candidate requirement is even considered
for evidence validation (see evidence_validator.py) or persistence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema" / "requirements.schema.json"

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    _SCHEMA = json.load(f)

_VALIDATOR = jsonschema.Draft7Validator(_SCHEMA)

_NUMERIC_OPERATORS = {"LT", "LTE", "GT", "GTE"}
_ORDERED_CATEGORICAL_FIELDS = {"applicant.english_level"}
_COLLECTION_OPERATORS = {"IN", "NOT_IN"}
_KNOWN_UNITS = {"years", "months", "weeks", "days", "points", "AUD", "percent", None}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]


def parse_llm_output(raw_text: str) -> tuple[dict | None, list[str]]:
    """Parse the raw LLM response string as JSON. Returns (parsed_dict_or_None, errors)."""
    cleaned = raw_text.strip()
    # tolerate accidental markdown fences even though the prompt forbids them
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned), []
    except json.JSONDecodeError as e:
        return None, [f"INVALID_JSON: {e}"]


def validate_schema(parsed: dict) -> ValidationResult:
    errors = [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in _VALIDATOR.iter_errors(parsed)]
    return ValidationResult(ok=not errors, errors=errors, warnings=[])


def validate_logic(requirement: dict) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    operator = requirement.get("operator")
    value = requirement.get("value")
    unit = requirement.get("unit")
    content_kind = requirement.get("content_kind")
    field = requirement.get("field")

    if (operator in _NUMERIC_OPERATORS and not isinstance(value, (int, float))
            and not (field in _ORDERED_CATEGORICAL_FIELDS and isinstance(value, str) and value.strip())):
        errors.append(f"operator={operator} requires a numeric value, got {value!r}")

    if operator in _COLLECTION_OPERATORS and not isinstance(value, list):
        errors.append(f"operator={operator} requires a list value, got {value!r}")

    if operator == "BETWEEN" and (not isinstance(value, list) or len(value) != 2):
        errors.append("operator=BETWEEN requires a 2-element [min, max] value")

    if unit not in _KNOWN_UNITS:
        warnings.append(f"unrecognized unit '{unit}' - not in controlled unit set, needs review")

    if content_kind == "REQUIREMENT" and field is None and operator not in ("REQUIRED", "NOT_REQUIRED", "UNKNOWN"):
        warnings.append("content_kind=REQUIREMENT with a comparison operator but no 'field' set")

    # Closed field vocabulary (v3): NOT_MODELED without a concept label would
    # lose the real legal/policy intent entirely - never silently accept that.
    # A warning (not a hard error) because the requirement itself is otherwise
    # sound; this routes it to NEEDS_REVIEW rather than discarding it.
    if field == "NOT_MODELED" and not (requirement.get("concept") or "").strip():
        warnings.append("field=NOT_MODELED but 'concept' is missing or empty - needs a human label")

    mandatory = requirement.get("mandatory")
    if mandatory is not None and not isinstance(mandatory, bool):
        errors.append(f"'mandatory' must be boolean or null, got {mandatory!r}")

    if requirement.get("confidence") is not None:
        c = requirement["confidence"]
        if not isinstance(c, (int, float)) or not (0.0 <= c <= 1.0):
            errors.append(f"'confidence' must be in [0.0, 1.0], got {c!r}")

    evidence = requirement.get("evidence") or []
    if not evidence:
        errors.append("requirement has no evidence entries")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
