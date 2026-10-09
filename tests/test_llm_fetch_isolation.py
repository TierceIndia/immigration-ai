"""An LLM provider's rate limit and a fetch host's refusal are unrelated.

Two different 429s exist in this system and they must not bleed into one
another:

* An LLM **provider** 429 is a quota on *our API key*. It is handled entirely
  inside the LLM client (backoff, then ``QuotaExhaustedError``). It must never
  open a fetch **host** cooldown — the host did nothing wrong, and pausing it
  because Gemini was busy would silently stop monitoring a healthy source.

* A fetch **host** 403/429 is the origin refusing *us*. It opens a host
  cooldown (see orchestration/host_cooldown.py). It must never change how the
  LLM layer retries — the two layers share a process, not a policy.

These tests pin both directions so a later refactor cannot couple them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.connection import get_session_factory
from db.models import (
    Base,
    Country,
    GovernmentSource,
    HostCooldown,
    ImmigrationProgram,
)
from extraction.llm_client import GeminiLLMClient, QuotaExhaustedError
from fetch.fetcher import FetchResult, FetchStatus
from orchestration.host_cooldown import active_cooldown, is_cooling
from utils.fetch_document import _record_fetch_failure

SOURCE_URL = "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/189"
REFUSING_URL = "https://refusing.example.invalid/189"


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def session(engine):
    s = get_session_factory(engine)()
    yield s
    s.close()


def _seed(session, source_id="SRC-AU-189", url=SOURCE_URL):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.merge(GovernmentSource(
        source_id=source_id, country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url=url, source_type="HTML", priority="PRIMARY",
        monitoring_method="WEB", crawl_frequency="DAILY", status="ACTIVE",
    ))
    session.commit()
    return source_id


def _refusal(url, status):
    return FetchResult(
        url=url, status=FetchStatus.ACCESS_BLOCKED, http_status=status,
        content_type=None, raw_bytes=None, duration_ms=5, error=f"HTTP {status}",
    )


def _run_persistent_llm_429(monkeypatch):
    """Drive one Gemini extraction through a wall of 429s.

    Returns ``(post_call_count, sleep_durations)`` with ``time.sleep`` stubbed,
    so it also records exactly how the LLM layer chose to back off.
    """
    monkeypatch.setenv("GEMINI_TIER", "free")
    client = GeminiLLMClient(api_key="test-dummy-key")
    with patch("time.sleep") as sleep_mock, patch(
        "requests.post", autospec=True
    ) as post_mock:
        post_mock.return_value = MagicMock(status_code=429, headers={})
        with pytest.raises(QuotaExhaustedError):
            client.extract({"chunks": [{"raw_text": "policy text"}]})
        sleeps = [call.args[0] for call in sleep_mock.call_args_list]
        return post_mock.call_count, sleeps


# ---------------------------------------------------------------------------
# Direction 1: an LLM 429 must not touch the fetch host cooldown table
# ---------------------------------------------------------------------------
def test_llm_429_opens_no_fetch_host_cooldown(session, monkeypatch):
    """The provider rate-limiting us is not the host refusing us."""
    _seed(session)

    call_count, sleeps = _run_persistent_llm_429(monkeypatch)

    # The LLM client did exhaust its own retry budget...
    assert call_count == 4  # 1 initial + 3 free-tier retries
    assert sleeps == [5, 15, 45]
    # ...but it left the fetch-layer cooldown state completely alone.
    assert session.query(HostCooldown).count() == 0
    assert active_cooldown(session, "immi.homeaffairs.gov.au") is None
    assert not is_cooling(session, SOURCE_URL)


def test_llm_429_leaves_an_existing_fetch_cooldown_untouched(session, monkeypatch):
    """A genuine host cooldown is not extended or cleared by an unrelated 429."""
    _seed(session, source_id="SRC-AU-189", url=REFUSING_URL)
    source = session.get(GovernmentSource, "SRC-AU-189")
    _record_fetch_failure(
        session, source, _refusal(REFUSING_URL, 403), datetime.now(timezone.utc)
    )
    session.commit()
    before = active_cooldown(session, "refusing.example.invalid")
    assert before is not None
    before_until, before_hits = before.cooldown_until, before.hits

    _run_persistent_llm_429(monkeypatch)

    session.expire_all()
    after = active_cooldown(session, "refusing.example.invalid")
    assert after is not None
    assert after.cooldown_until == before_until
    assert after.hits == before_hits


# ---------------------------------------------------------------------------
# Direction 2: a fetch 403/429 must not change LLM retry behaviour
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [403, 429])
def test_fetch_refusal_does_not_change_llm_retry_behaviour(session, monkeypatch, status):
    _seed(session, url=SOURCE_URL)

    before = _run_persistent_llm_429(monkeypatch)
    assert before == (4, [5, 15, 45])

    source = session.get(GovernmentSource, "SRC-AU-189")
    _record_fetch_failure(
        session, source, _refusal(SOURCE_URL, status), datetime.now(timezone.utc)
    )
    session.commit()
    assert is_cooling(session, SOURCE_URL)

    after = _run_persistent_llm_429(monkeypatch)

    # Identical call count and identical backoff schedule: the host's refusal
    # changed nothing about how (or whether) the LLM layer retries.
    assert after == before


def test_fetch_refusal_leaves_llm_retry_policy_delays_untouched(session):
    from llm_governance import LLMGovernance

    policy = LLMGovernance().retry_policy
    before = [policy.delay_for_attempt(i) for i in range(4)]

    _seed(session, url=SOURCE_URL)
    source = session.get(GovernmentSource, "SRC-AU-189")
    _record_fetch_failure(
        session, source, _refusal(SOURCE_URL, 429), datetime.now(timezone.utc)
    )
    session.commit()
    assert is_cooling(session, SOURCE_URL)

    after = [policy.delay_for_attempt(i) for i in range(4)]
    assert after == before
