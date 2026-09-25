"""Regression coverage for specific source_registry.seed.json content fixes.

Not a general schema/shape validator (that's tests/test_seed_registry_db.py) —
this asserts specific, previously-wrong field values stay fixed, without
hardcoding values that are expected to legitimately change over time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SEED_PATH = ROOT / "sources" / "source_registry.seed.json"


def _load_source(source_id: str) -> dict:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for s in data["government_sources"]:
        if s["source_id"] == source_id:
            return s
    raise AssertionError(f"{source_id} not found in seed registry")


class TestMigrationRegsSourceIsRealText:
    """SRC-AU-MIGRATION-REGS-1994 previously pointed at
    /F1996B03551/latest/text, a Federal Register of Legislation index/citation
    page (an interactive expand-tree with no static text — 8 chunks, ~400
    chars). Fixed 2026-09-25 to a direct PDF volume deep-link, live-verified to
    contain the real Subclass 189/190/491 Schedule 2 criteria text.

    The exact URL embeds a compilation date that will need updating whenever
    the regulation is next re-compiled — so this only asserts the URL *shape*
    (PDF volume deep-link, not the old index page), not the literal date.
    """

    def test_source_type_is_pdf_not_html(self):
        source = _load_source("SRC-AU-MIGRATION-REGS-1994")
        assert source["source_type"] == "PDF"

    def test_url_is_a_pdf_volume_deep_link_not_the_index_page(self):
        source = _load_source("SRC-AU-MIGRATION-REGS-1994")
        url = source["url"]
        assert url != "https://www.legislation.gov.au/F1996B03551/latest/text", (
            "reverted to the old index/citation page with no static text"
        )
        assert "/text/original/pdf/" in url
        assert url.startswith("https://www.legislation.gov.au/F1996B03551/")

    def test_url_points_at_volume_2_which_contains_schedule_2_010_to_801(self):
        """Volume 1 is just Part 1 Preliminary/definitions; Volume 2 is the
        volume live-verified (2026-09-25) to contain Schedule 2, Subclasses
        010 to 801 — which covers 189/190/491/482/500/600."""
        source = _load_source("SRC-AU-MIGRATION-REGS-1994")
        assert source["url"].rstrip("/").endswith("/2")
