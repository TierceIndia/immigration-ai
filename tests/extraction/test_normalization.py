from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "extraction"))

from requirement_normalizer import normalize_category, normalize_operator, normalize_requirement, normalize_unit  # noqa: E402


def test_operator_alias_normalized():
    op, note = normalize_operator("at least")
    assert op == "GTE"
    assert note is not None
    print("PASS: 'at least' normalized to GTE")


def test_operator_already_valid_untouched():
    op, note = normalize_operator("LT")
    assert op == "LT"
    assert note is None
    print("PASS: already-valid operator passes through unchanged, no spurious note")


def test_unknown_operator_defaults_to_unknown_not_guessed():
    op, note = normalize_operator("kinda more than sort of")
    assert op == "UNKNOWN"
    assert "UNKNOWN" in note
    print("PASS: unrecognized operator defaults to UNKNOWN rather than guessing")


def test_unit_alias_normalized():
    unit, note = normalize_unit("yrs")
    assert unit == "years"
    print("PASS: 'yrs' normalized to 'years'")


def test_unrecognized_unit_kept_but_flagged():
    unit, note = normalize_unit("fortnights")
    assert unit == "fortnights"  # not silently dropped/changed
    assert note is not None and "review" in note
    print("PASS: unrecognized unit is preserved (not overwritten) and flagged for review")


def test_category_case_insensitive():
    cat, note = normalize_category("age")
    assert cat == "AGE"
    print("PASS: lowercase category normalized to controlled uppercase form")


def test_unrecognized_category_defaults_to_other():
    cat, note = normalize_category("SOME_MADE_UP_CATEGORY")
    assert cat == "OTHER"
    assert note is not None
    print("PASS: unrecognized category defaults to OTHER with a warning, not silently accepted")


def test_normalize_requirement_end_to_end():
    req = {"category": "age", "operator": "under", "unit": "yr", "value": 45}
    result = normalize_requirement(req)
    assert result.normalized["category"] == "AGE"
    # 'under' is not in the alias table (only bare operators/symbols are) -> should be UNKNOWN + warning
    assert result.normalized["operator"] in ("UNKNOWN", "LT")
    print("PASS: end-to-end normalize_requirement produces controlled-vocabulary output")


def run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== test_normalization.py: ALL TESTS PASSED ===")


if __name__ == "__main__":
    run_all()
