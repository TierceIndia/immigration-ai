"""Runs every test module in order and exits non-zero if any fails. Use this as the single CI
entry point:

    python tests/run_all_tests.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODULES = [
    "tests/test_pipeline_offline.py",
    "tests/test_extractor_ha_api.py",
    "tests/phase2/test_schema.py",
    "tests/phase2/test_evidence.py",
    "tests/phase2/test_normalization.py",
    "tests/phase2/test_deduplication.py",
    "tests/phase2/test_extraction.py",
    "tests/phase2/test_versioning.py",
    "tests/phase2/test_gold_dataset.py",
    "tests/phase2/test_real_gold_evaluation.py",
]


def main() -> int:
    failures = []
    for module in MODULES:
        print(f"\n{'=' * 70}\nRUNNING {module}\n{'=' * 70}")
        result = subprocess.run([sys.executable, module], cwd=ROOT)
        if result.returncode != 0:
            failures.append(module)

    print(f"\n{'=' * 70}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(MODULES)} modules failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"ALL {len(MODULES)} TEST MODULES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
