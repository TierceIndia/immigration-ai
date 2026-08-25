"""Regression tests for Home Affairs HTML text normalization."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from extractor_ha_api import strip_html  # noqa: E402


def test_inline_elements_preserve_word_boundaries():
    assert strip_html("<p>read<a>information</a> on visas</p>") == "read information on visas"


def test_known_upstream_joined_words_are_repaired():
    assert strip_html("meet ourhealth requirement and thecharacter requirement") == (
        "meet our health requirement and the character requirement"
    )


def test_windows_1252_mojibake_is_repaired():
    assert strip_html("applicantâ€™s invitation") == "applicant’s invitation"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_extractor_ha_api.py: ALL TESTS PASSED")
