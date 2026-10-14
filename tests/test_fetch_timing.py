"""Tests for fetch/timing.py — pure data/formatting, no network, no browser."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch.timing import SourceTiming, format_timing_table


class TestSourceTimingLogFields:
    def test_as_log_fields_includes_every_named_field(self):
        t = SourceTiming(
            source_id="SRC-AU-TEST", method="http", limiter_wait_ms=120,
            http_ms=340, extract_ms=15, llm_calls=2, llm_wait_ms=900, total_ms=1400,
        )
        fields = t.as_log_fields()
        for key in (
            "source_id", "method", "limiter_wait_ms", "http_ms", "browser_launch_ms",
            "goto_ms", "selector_wait_ms", "selector_timed_out", "extract_ms",
            "llm_calls", "llm_wait_ms", "total_ms",
        ):
            assert key in fields, key

    def test_unset_stage_is_none_not_zero(self):
        """A stage that never ran must read None, not a falsely-instant 0."""
        t = SourceTiming(source_id="SRC-AU-TEST", method="http")
        assert t.browser_launch_ms is None
        assert t.goto_ms is None
        assert t.selector_wait_ms is None
        assert t.selector_timed_out is None


class TestFormatTimingTable:
    def test_empty_list_says_so(self):
        assert format_timing_table([]) == "(no sources fetched)"

    def test_table_has_a_total_row_summing_numeric_columns(self):
        rows = [
            SourceTiming(source_id="SRC-A", method="http", limiter_wait_ms=100,
                         http_ms=200, extract_ms=10, llm_calls=1, llm_wait_ms=500, total_ms=800),
            SourceTiming(source_id="SRC-B", method="browser", limiter_wait_ms=50,
                         browser_launch_ms=300, goto_ms=400, selector_wait_ms=8000,
                         selector_timed_out=True, extract_ms=5, llm_calls=0,
                         llm_wait_ms=0, total_ms=8750),
        ]
        table = format_timing_table(rows)
        assert "SRC-A" in table
        assert "SRC-B" in table
        assert "TOTAL" in table
        lines = table.splitlines()
        total_line = next(l for l in lines if l.startswith("TOTAL"))
        # total_ms column: 800 + 8750 = 9550
        assert "9550" in total_line
        # limiter_wait_ms column: 100 + 50 = 150
        assert "150" in total_line

    def test_none_stage_renders_as_dash_not_zero(self):
        rows = [SourceTiming(source_id="SRC-A", method="http", total_ms=50)]
        table = format_timing_table(rows)
        # browser_launch_ms column must show '-' (never ran), not '0'.
        header_cols = table.splitlines()[0].split(" | ")
        data_cols = table.splitlines()[2].split(" | ")
        idx = header_cols.index("browser_launch_ms")
        assert data_cols[idx].strip() == "-"

    def test_boolean_renders_as_yes_no(self):
        rows = [
            SourceTiming(source_id="SRC-A", method="browser", selector_timed_out=True, total_ms=1),
            SourceTiming(source_id="SRC-B", method="browser", selector_timed_out=False, total_ms=1),
        ]
        table = format_timing_table(rows)
        assert "yes" in table
        assert "no" in table
