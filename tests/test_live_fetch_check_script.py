"""The live-fetch gate script: it must be impossible to run by accident.

scripts/live_fetch_check.py is the only thing in the repo that contacts a real
government host, so these tests pin the guards rather than the output: the
opt-in env var is mandatory, ``--url`` mode never opens a database connection,
and the exit code says exactly what happened. The fetch itself is faked - no
browser, no network.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fetch.fetcher import FetchResult, FetchStatus  # noqa: E402

PDF_BYTES = b"%PDF-1.4 " + b"a" * 200
URL = "https://www.legislation.gov.au/F1996B03551/2026-07-01/text/original/pdf/2"


@pytest.fixture()
def script():
    """Load the script as a module (it lives outside any package)."""
    path = ROOT / "scripts" / "live_fetch_check.py"
    spec = importlib.util.spec_from_file_location("live_fetch_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def no_database(monkeypatch):
    """Make any database use an immediate, loud failure."""
    import db.connection as connection

    def explode(*args, **kwargs):
        raise AssertionError("--url mode must not touch the database")

    monkeypatch.setattr(connection, "get_engine", explode)


def _ok(body=PDF_BYTES):
    return FetchResult(
        url=URL, status=FetchStatus.OK, http_status=200,
        content_type="application/pdf", raw_bytes=body, duration_ms=900,
        error=None, acquisition="playwright_request", limiter_wait_ms=0,
    )


def _blocked():
    return FetchResult(
        url=URL, status=FetchStatus.ACCESS_BLOCKED, http_status=403,
        content_type="text/html", raw_bytes=None, duration_ms=500,
        error="HTTP 403", acquisition="playwright_page",
    )


def _patch_fetch(monkeypatch, result):
    calls: list[str] = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return result

    monkeypatch.setattr("fetch.backend.fetch", fake_fetch)
    return calls


def test_it_refuses_without_the_opt_in_and_fetches_nothing(script, monkeypatch):
    monkeypatch.delenv("LIVE_FETCH_OK", raising=False)
    calls = _patch_fetch(monkeypatch, _ok())

    assert script.main(["--url", URL]) == 3
    assert calls == [], "no opt-in means no request, not even a refused one"


def test_url_mode_never_opens_a_database_connection(
    script, monkeypatch, no_database, capsys,
):
    monkeypatch.setenv("LIVE_FETCH_OK", "1")
    _patch_fetch(monkeypatch, _ok())

    assert script.main(["--url", URL, "--source-type", "PDF"]) == 0
    out = capsys.readouterr().out
    assert "read from              arguments" in out


def test_a_matching_hash_is_exit_zero(script, monkeypatch, no_database, capsys):
    from change_detection.hashing import sha256_bytes

    monkeypatch.setenv("LIVE_FETCH_OK", "1")
    _patch_fetch(monkeypatch, _ok())

    code = script.main([
        "--url", URL, "--source-type", "PDF",
        "--expect-hash", sha256_bytes(PDF_BYTES),
    ])

    assert code == 0
    assert "MATCH" in capsys.readouterr().out


def test_a_different_hash_is_exit_one(script, monkeypatch, no_database, capsys):
    monkeypatch.setenv("LIVE_FETCH_OK", "1")
    _patch_fetch(monkeypatch, _ok())

    code = script.main([
        "--url", URL, "--source-type", "PDF", "--expect-hash", "0" * 64,
    ])

    assert code == 1
    assert "DIFFERENT" in capsys.readouterr().out


def test_a_refusal_is_exit_two_and_is_not_retried(
    script, monkeypatch, no_database, capsys,
):
    monkeypatch.setenv("LIVE_FETCH_OK", "1")
    calls = _patch_fetch(monkeypatch, _blocked())

    code = script.main(["--url", URL, "--source-type", "PDF"])

    assert code == 2
    assert len(calls) == 1, "one attempt only - a refusal is never retried"
    out = capsys.readouterr().out
    assert "ACCESS_BLOCKED" in out


def test_an_unreachable_database_says_so_instead_of_crashing(
    script, monkeypatch, capsys,
):
    monkeypatch.setenv("LIVE_FETCH_OK", "1")
    import db.connection as connection

    def unreachable(*args, **kwargs):
        raise RuntimeError("could not connect")

    monkeypatch.setattr(connection, "get_engine", unreachable)
    calls = _patch_fetch(monkeypatch, _ok())

    assert script.main(["SRC-AU-MIGRATION-REGS-1994"]) == 3
    assert calls == [], "a DB failure must not still fire a live request"
    assert "database unavailable" in capsys.readouterr().err
