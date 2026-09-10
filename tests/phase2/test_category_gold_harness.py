"""Phase 3: category-aware gold dataset + prompt-contract integrity.

The hard invariant this suite guards is ZERO cross-category contamination: a Student
source's gold dataset must never be validated against the Skilled (or Work / Visitor)
extraction prompt contract, and vice versa. We enforce it two ways, independently:

  1. Prompt-contract fingerprints - each category's prompt file must bear its own
     `Prompt version identifier` token and must be byte-distinct from every other
     category's contract, so the wiring in llm_client._get_prompt_path cannot silently
     route one category to a foreign contract.
  2. Dataset ownership - every gold JSON under data/gold/<visa_category>/ must declare a
     matching top-level `visa_category`, and must never be discovered as belonging to a
     different category directory.

We also assert the anti-hallucination posture of the placeholders: the work / student /
visitor directories are explicitly marked AWAITING_REAL_SOURCE with ZERO items (we never
fabricate evidence_text); the skilled directory holds our real, verbatim gold items.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from phase2 import category_gold as cg  # noqa: E402

PROMPT_VERSION = cg.PROMPT_VERSION
CANONICAL = cg.CANONICAL_CATEGORIES


def _read_prompt(cat):
    return (cg.PROMPT_ROOT / cat / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")


def test_all_canonical_categories_are_contamination_free():
    """Every category resolves to its own prompt contract (no foreign contract)."""
    contracts = cg.verify_category_contracts()
    for cat in CANONICAL:
        info = contracts[cat]
        assert info["contaminated"] is False, (
            f"{cat} resolves to contract {info['actual_signature']!r}, "
            f"expected its own {info['expected_signature']!r}"
        )
        assert info["actual_signature"] is not None


@pytest.mark.parametrize(
    "cat,expected_token",
    [
        ("skilled", "requirement_extraction_v2"),
        ("work", "requirement_extraction_v2_work"),
        ("student", "requirement_extraction_v2_student"),
        ("visitor", "requirement_extraction_v2_visitor"),
    ],
)
def test_each_category_prompt_wears_own_signature(cat, expected_token):
    assert cg.read_prompt_token(cat) == expected_token


def test_category_prompts_are_mutually_distinct():
    """Byte-uniqueness: no two categories share a single contract file's content. This
    makes it impossible for a Student source to accidentally be scored against the Skilled
    contract (or any other category's)."""
    contents = {cat: _read_prompt(cat) for cat in CANONICAL}
    for a in CANONICAL:
        for b in CANONICAL:
            if a != b:
                assert contents[a] != contents[b], f"{a} and {b} share a contract"


def test_skilled_prompt_is_the_base_contract():
    """The skilled contract is the base v2 prompt (no category suffix) - it must not carry
    a foreign _work/_student/_visitor token."""
    skilled_text = _read_prompt("skilled")
    for other in ("work", "student", "visitor"):
        token = cg.prompt_signature(other)
        assert token not in skilled_text, (
            f"skilled prompt unexpectedly contains {other} contract marker {token}"
        )


def test_no_gold_dataset_leaks_across_categories():
    """Every gold dataset must live in, and declare, exactly its own visa category."""
    report = cg.all_invariants_hold()
    leaks = report["cross_category_leak"]
    assert leaks == [], f"cross-category gold contamination detected: {leaks}"
    assert report["contract_violations"] == []


def test_all_four_category_dirs_exist():
    report = cg.all_invariants_hold()
    for cat in CANONICAL:
        assert report["datasets"][cat]["exists"]


def test_skilled_gold_holds_real_items():
    """The skilled directory is the populated one (real, verbatim gold)."""
    skilled = cg.scan_gold_datasets()["skilled"]
    assert skilled["item_count"] > 0
    # Every skilled dataset file must declare visa_category == skilled
    for f in skilled["files"]:
        assert f["declared_visa_category"] == "skilled"


def test_work_student_visitor_are_honest_placeholders():
    """Anti-hallucination: the non-skilled category dirs must be explicitly awaiting real
    source content and contain ZERO gold items (we never fabricate evidence_text)."""
    for cat in ("work", "student", "visitor"):
        dataset = cg.scan_gold_datasets()[cat]
        assert dataset["exists"]
        assert dataset["item_count"] == 0, (
            f"{cat} must not contain fabricated gold; if real content was added it should "
            f"go through the real-source review, not be stubbed here"
        )
        for f in dataset["files"]:
            data = json.loads((cg.GOLD_ROOT / cat / f["file"]).read_text(encoding="utf-8"))
            assert data.get("_status") == "AWAITING_REAL_SOURCE"
            assert data.get("visa_category") == cat
            assert data.get("items") == []


def test_evidence_fingerprint_exists_globally():
    """Any file that DOES claim gold items must provide verbatim evidence for each item
    (the anti-fabrication guarantee). This guard runs across all category dirs."""
    report = cg.all_invariants_hold()
    for cat in CANONICAL:
        for f in report["datasets"][cat]["files"]:
            data = json.loads((cg.GOLD_ROOT / cat / f["file"]).read_text(encoding="utf-8"))
            items = data.get("items", [])
            for it in items:
                ev = it.get("evidence_text")
                assert isinstance(ev, str) and ev.strip(), (
                    f"{cat}/{f['file']} item {it.get('gold_id')} has no verbatim evidence"
                )
