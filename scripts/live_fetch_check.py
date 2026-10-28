"""One live fetch of one source, read-only, for a human-gated verification.

This is the ONLY script in the repo that contacts a real government host, and
it does so exactly once per invocation, for one source, and writes nothing:
no document version, no fetch_log row, no last_hash update, no snapshot. It
exists so the Playwright port can be proven against a real source before the
retired path is deleted (Phase 1d) - the bytes a real browser brings back must
hash to the same value the previous path recorded.

Because it is live, it refuses to run unless the operator opts in explicitly:

    LIVE_FETCH_OK=1 python scripts/live_fetch_check.py SRC-AU-MIGRATION-REGS-1994

The source's URL, type and stored hash are read from the database. Where the
database is not reachable from where the browser runs, pass them directly and
no database connection is attempted at all:

    LIVE_FETCH_OK=1 python scripts/live_fetch_check.py \
        --url https://example.gov.au/doc.pdf --source-type PDF --expect-hash <sha256>

Politeness still applies in full: the process-wide HostLimiter spacing and the
host's robots.txt Crawl-delay are consulted by the same code path production
uses. A refusal (401/403/429) is reported and the script stops - it never
retries, never changes mechanism, and never sends anything but the honest
User-Agent from FETCH_USER_AGENT.

Exit codes:
    0  fetch succeeded AND the hash matched the expected one (or none was given)
    1  fetch succeeded but the hash differs from the expected one
    2  the fetch itself did not succeed (refusal, fault, challenge, ...)
    3  misuse (no opt-in, unknown source, DB unavailable)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DEFAULT_SOURCE_ID = "SRC-AU-MIGRATION-REGS-1994"
OPT_IN_ENV = "LIVE_FETCH_OK"


def _row(label: str, value) -> str:
    return f"  {label:<22} {value}"


def _from_database(source_id: str, conditional: bool):
    """Look up one source. Returns a dict, or None when it cannot be read."""
    from db.connection import get_engine, get_session_factory
    from db.models import GovernmentSource

    engine = get_engine()
    session = get_session_factory(engine)()
    try:
        source = session.get(GovernmentSource, source_id)
        if source is None:
            print(f"unknown source_id: {source_id}", file=sys.stderr)
            return None
        return {
            "url": source.url,
            "source_type": source.source_type or "HTML",
            "expected_hash": source.last_hash,
            "last_checked_at": source.last_checked_at,
            "etag": source.etag if conditional else None,
            "last_modified": source.last_modified if conditional else None,
        }
    finally:
        # Read-only by construction: nothing was added or changed, and the
        # session is closed without a commit.
        session.rollback()
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "source_id", nargs="?", default=DEFAULT_SOURCE_ID,
        help=f"government_sources.source_id to fetch (default: {DEFAULT_SOURCE_ID})",
    )
    parser.add_argument(
        "--url",
        help="fetch this URL instead of looking the source up in the database "
             "(no database connection is made at all)",
    )
    parser.add_argument(
        "--source-type", default="HTML",
        help="HTML or PDF; only used together with --url (default: HTML)",
    )
    parser.add_argument(
        "--expect-hash",
        help="sha256 of the bytes this fetch should reproduce; only used "
             "together with --url",
    )
    parser.add_argument(
        "--conditional", action="store_true",
        help="send the stored ETag/Last-Modified. A 304 then returns no body, "
             "so no hash can be computed - use this only to verify that "
             "conditional GET works against the real host.",
    )
    args = parser.parse_args(argv)

    if os.getenv(OPT_IN_ENV) != "1":
        print(
            f"refusing to run: this makes a LIVE request to a government host.\n"
            f"Re-run with {OPT_IN_ENV}=1 if that is what you intend.",
            file=sys.stderr,
        )
        return 3

    from change_detection.hashing import sha256_bytes
    from fetch.backend import active_backend, fetch
    from fetch.fetcher import user_agent
    from fetch.playwright_client import log_proxy_configuration

    if args.url:
        source = {
            "url": args.url,
            "source_type": (args.source_type or "HTML").upper(),
            "expected_hash": args.expect_hash,
            "last_checked_at": "(not read - --url given)",
            "etag": None,
            "last_modified": None,
        }
        origin = "arguments"
    else:
        try:
            source = _from_database(args.source_id, args.conditional)
        except Exception as exc:
            # The engine is lazy, so a bad DATABASE_URL only shows up here.
            print(f"database unavailable: {exc}", file=sys.stderr)
            print(
                "Pass --url / --source-type / --expect-hash to run without "
                "the database.", file=sys.stderr,
            )
            return 3
        if source is None:
            return 3
        origin = "database"

    print("LIVE FETCH CHECK (read-only - nothing is written)")
    print(_row("source_id", args.source_id if origin == "database" else "(n/a)"))
    print(_row("read from", origin))
    print(_row("url", source["url"]))
    print(_row("source_type", source["source_type"]))
    print(_row("backend", active_backend()))
    print(_row("user_agent", user_agent()))
    print(_row("conditional", "yes" if args.conditional else "no (full body)"))
    print(_row("last_checked_at", source["last_checked_at"]))
    log_proxy_configuration()
    print()

    result = fetch(
        source["url"], source_type=source["source_type"],
        etag=source["etag"], last_modified=source["last_modified"],
    )

    print("RESULT")
    print(_row("fetch_status", result.status.value))
    print(_row("acquisition", result.acquisition or "unknown"))
    print(_row("http_status", result.http_status))
    print(_row("content_type", result.content_type))
    print(_row("bytes", len(result.raw_bytes) if result.raw_bytes else 0))
    print(_row("not_modified", result.not_modified))
    print(_row("etag", result.etag))
    print(_row("last_modified", result.last_modified))
    if result.error:
        print(_row("error", result.error))
    print()

    print("TIMING (ms)")
    for field in (
        "duration_ms", "limiter_wait_ms", "browser_launch_ms", "goto_ms",
        "http_ms", "selector_wait_ms",
    ):
        print(_row(field, getattr(result, field, None)))
    print(_row("selector_timed_out", result.selector_timed_out))
    print()

    if not result.ok or not result.raw_bytes:
        print("VERDICT  fetch did not produce a body - nothing to compare.")
        if result.not_modified:
            print("         (304 not modified: re-run without --conditional "
                  "to get a body to hash)")
        return 2

    fetched_hash = sha256_bytes(result.raw_bytes)
    expected = source["expected_hash"]
    print("HASH")
    print(_row("fetched", fetched_hash))
    print(_row("expected", expected or "(none given)"))
    print()

    if not expected:
        print("VERDICT  no expected hash to compare against; fetch succeeded.")
        return 0
    if fetched_hash == expected:
        print("VERDICT  MATCH - the Playwright path reproduces the stored bytes.")
        return 0
    print(
        "VERDICT  DIFFERENT - either the source genuinely changed since the "
        f"last sync ({source['last_checked_at']}), or this path does not "
        "reproduce the same bytes. Do not delete the retired path until this "
        "is understood."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
