"""Per-source fetch/extraction timing — measure before removing waste.

One ``SourceTiming`` per source per sync attempt, carrying every stage a
request can spend time in. Fields are optional/None when a stage never ran
(e.g. ``browser_launch_ms`` is None for a source resolved by plain HTTP) —
None means "did not happen", 0 means "happened and took no measurable time".

Nothing here makes a network call or imports requests/patchright: it is pure
data + formatting, so it can be unit-tested without a live source and without
a browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceTiming:
    source_id: str
    method: str  # "http" | "browser"
    limiter_wait_ms: int = 0
    http_ms: int | None = None
    browser_launch_ms: int | None = None
    goto_ms: int | None = None
    selector_wait_ms: int | None = None
    selector_timed_out: bool | None = None
    extract_ms: int | None = None
    llm_calls: int = 0
    llm_wait_ms: int = 0
    total_ms: int = 0

    def as_log_fields(self) -> dict:
        """Flat dict for a structured log line's ``extra``."""
        return {
            "source_id": self.source_id,
            "method": self.method,
            "limiter_wait_ms": self.limiter_wait_ms,
            "http_ms": self.http_ms,
            "browser_launch_ms": self.browser_launch_ms,
            "goto_ms": self.goto_ms,
            "selector_wait_ms": self.selector_wait_ms,
            "selector_timed_out": self.selector_timed_out,
            "extract_ms": self.extract_ms,
            "llm_calls": self.llm_calls,
            "llm_wait_ms": self.llm_wait_ms,
            "total_ms": self.total_ms,
        }


_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_id", "source_id"),
    ("method", "method"),
    ("limiter_wait_ms", "limiter_wait_ms"),
    ("http_ms", "http_ms"),
    ("browser_launch_ms", "browser_launch_ms"),
    ("goto_ms", "goto_ms"),
    ("selector_wait_ms", "selector_wait_ms"),
    ("selector_timed_out", "selector_timed_out"),
    ("extract_ms", "extract_ms"),
    ("llm_calls", "llm_calls"),
    ("llm_wait_ms", "llm_wait_ms"),
    ("total_ms", "total_ms"),
)


def _cell(value) -> str:
    if value is None:
        return "-"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def format_timing_table(rows: list[SourceTiming]) -> str:
    """Plain-text table summarising one sync run's per-source timings.

    One row per source plus a trailing TOTAL row (sum of every numeric
    column, including every source whether it succeeded or not — the point
    is where the wall-clock went, not just the successes).
    """
    if not rows:
        return "(no sources fetched)"

    headers = [h for h, _ in _COLUMNS]
    data = [[_cell(getattr(r, attr)) for _, attr in _COLUMNS] for r in rows]

    totals: list[str] = ["TOTAL", "-"]
    for _, attr in _COLUMNS[2:]:
        values = [getattr(r, attr) for r in rows]
        numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        totals.append(str(sum(numeric)) if numeric else "-")
    data.append(totals)

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in data))
        for i in range(len(headers))
    ]

    def _fmt_row(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [_fmt_row(headers), "-+-".join("-" * w for w in widths)]
    lines.extend(_fmt_row(row) for row in data[:-1])
    lines.append("-+-".join("-" * w for w in widths))
    lines.append(_fmt_row(data[-1]))
    return "\n".join(lines)
