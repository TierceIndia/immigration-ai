"""Category-aware gold dataset harness.

Phase 3: each visa category (skilled, work, student, visitor) owns its gold dataset
under data/gold/<category>/ and is evaluated against ITS OWN prompt contract
(prompts/<category>/requirement_extraction_v2.md). The single most important invariant
this module enforces is ZERO cross-category contamination: a gold item that SOUNDS like a
student visa requirement must never be validated against the skilled prompt contract, and
vice versa.

This module is data-driven and pure: it fingerprints the on-disk prompt contracts and the
gold item categories, then reports any contamination. It does NOT run the LLM extraction
pipeline (that belongs to the per-category evaluation tests) - it only guarantees that the
datasets are structurally sound and correctly bound to their category's prompt contract,
so no real extraction run can silently use the wrong contract.

Anti-hallucination rule (project-wide): gold evidence_text must be verbatim from a real
source. The work/student/visitor directories currently hold only structural metadata /
placeholder files (no fabricated evidence) because live Home Affairs content is HTTP 403
blocked to automated fetchers. They are fillable once real content is available; this
harness validates whatever is present and stays green for empty-but-documented datasets.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GOLD_ROOT = ROOT / "data" / "gold"
PROMPT_ROOT = ROOT / "prompts"
PROMPT_VERSION = "requirement_extraction_v2"

# Canonical categories that own gold datasets + prompt contracts.
CANONICAL_CATEGORIES = ("skilled", "work", "student", "visitor")

# signature_regex: the string that must appear at the top of a category's prompt file to
# prove it is that category's own contract. The skilled contract is the base v2 prompt
# (no `_skilled` suffix), so its signature is the bare version token. The others embed
# their category name: requirement_extraction_v2_work / _student / _visitor.
SIGNATURE_RE = re.compile(r"Prompt version identifier:\s*`(?P<token>[\w]+)`")
SKILLED_SIGNATURE = PROMPT_VERSION
_NON_SKILLED_SIGNATURE = {c: f"{PROMPT_VERSION}_{c}" for c in ("work", "student", "visitor")}


def _canonical(p: Path) -> str:
    return p.name.lower()


def prompt_signature(visa_category: str) -> str:
    """The signature token that must head the category's prompt contract."""
    cat = visa_category.lower()
    if cat == "skilled":
        return SKILLED_SIGNATURE
    return _NON_SKILLED_SIGNATURE[cat]


def read_prompt_token(visa_category: str) -> str | None:
    """Return the `Prompt version identifier` token actually found in the category's file."""
    cat = visa_category.lower()
    path = PROMPT_ROOT / cat / f"{PROMPT_VERSION}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = SIGNATURE_RE.search(text)
    return m.group("token") if m else None


def category_is_contaminated(visa_category: str) -> bool:
    """True if the category's prompt does not carry its OWN signature (i.e. it would be
    validated against the wrong contract, or a foreign contract)."""
    token = read_prompt_token(visa_category)
    if token is None:
        return True
    return token != prompt_signature(visa_category)


def _load_gold_datasets(gold_dir: Path) -> list[tuple[Path, dict]]:
    """Yield (path, dataset) for every well-formed gold JSON under a category directory."""
    datasets: list[tuple[Path, dict]] = []
    if not gold_dir.exists():
        return datasets
    for path in sorted(gold_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            datasets.append((path, data))
    return datasets


def verify_category_contracts() -> dict:
    """Check every canonical category resolves to its own distinct contract."""
    results = {}
    for cat in CANONICAL_CATEGORIES:
        results[cat] = {
            "category": cat,
            "prompt_file": f"prompts/{cat}/{PROMPT_VERSION}.md",
            "expected_signature": prompt_signature(cat),
            "actual_signature": read_prompt_token(cat),
            "contaminated": category_is_contaminated(cat),
        }
    return results


def scan_gold_datasets() -> dict:
    """Inventory of gold datasets per visa-category directory, marking any dataset whose
    declared `visa_category` (top-level metadata) disagrees with the directory that hosts it.
    Zero cross-category contamination means: a gold dataset living under data/gold/skilled/
    MUST declare visa_category == 'skilled' and must never be evaluated with the work /
    student / visitor prompt contract."""
    report = {}
    for cat in CANONICAL_CATEGORIES:
        gold_dir = GOLD_ROOT / cat
        files: list[dict] = []
        item_count = 0
        leak = []
        for path, data in _load_gold_datasets(gold_dir):
            declared = str(data.get("visa_category", "")).strip().lower()
            items = data.get("items", [])
            item_count += len(items) if isinstance(items, list) else 0
            entry = {
                "file": path.name,
                "declared_visa_category": declared or "<unset>",
                "item_count": len(items) if isinstance(items, list) else 0,
            }
            files.append(entry)
            if declared != cat:
                leak.append(
                    {
                        "file": path.name,
                        "host_directory": cat,
                        "declared_visa_category": declared or "<unset>",
                    }
                )
        report[cat] = {
            "directory": f"data/gold/{cat}",
            "exists": gold_dir.exists(),
            "item_count": item_count,
            "files": files,
            "cross_category_leak": leak,
        }
    return report


def all_invariants_hold() -> dict:
    """Aggregate the structural invariants: every contract is non-contaminated and no gold
    item crosses category boundaries."""
    contracts = verify_category_contracts()
    datasets = scan_gold_datasets()

    contract_violations = [
        c["category"] for c in contracts.values() if c["contaminated"]
    ]
    gold_categories = [
        cat for cat, ds in datasets.items() if ds["exists"]
    ]

    return {
        "contract_violations": contract_violations,
        "categories_with_gold_dirs": gold_categories,
        "cross_category_leak": [
            le
            for ds in datasets.values()
            for le in ds["cross_category_leak"]
        ],
        "prompt_contracts": contracts,
        "datasets": datasets,
    }


if __name__ == "__main__":
    print(json.dumps(all_invariants_hold(), indent=2, ensure_ascii=False))
