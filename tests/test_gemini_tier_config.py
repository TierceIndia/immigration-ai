"""Regression tests for Part 2d of the Gemini free-tier quota fix: a
GEMINI_TIER env var (free|paid) tunes both the shared governance's proactive
RateLimiter RPM and GeminiLLMClient's retry ceiling, defaulting conservatively
to the free tier so a deployment must opt into "paid" explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_governance import (
    DEFAULT_GEMINI_FREE_TIER_RPM,
    DEFAULT_GEMINI_PAID_TIER_RPM,
    get_gemini_rpm,
    get_gemini_tier,
    get_shared_governance,
    reset_shared_governance,
)
from extraction.llm_client import GeminiLLMClient, QuotaExhaustedError, _gemini_retry_config


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("GEMINI_TIER", raising=False)
    monkeypatch.delenv("GEMINI_FREE_TIER_RPM", raising=False)
    monkeypatch.delenv("GEMINI_PAID_TIER_RPM", raising=False)
    reset_shared_governance()
    yield
    reset_shared_governance()


class TestGeminiTierResolution:
    def test_defaults_to_free_tier(self):
        assert get_gemini_tier() == "free"
        assert get_gemini_rpm() == DEFAULT_GEMINI_FREE_TIER_RPM

    def test_paid_tier_opt_in(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "paid")
        assert get_gemini_tier() == "paid"
        assert get_gemini_rpm() == DEFAULT_GEMINI_PAID_TIER_RPM

    def test_tier_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "PAID")
        assert get_gemini_tier() == "paid"

    def test_unsupported_tier_fails_fast(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "enterprise")
        with pytest.raises(ValueError, match="Unsupported GEMINI_TIER"):
            get_gemini_tier()

    def test_free_tier_rpm_override(self, monkeypatch):
        monkeypatch.setenv("GEMINI_FREE_TIER_RPM", "7")
        assert get_gemini_rpm() == 7

    def test_paid_tier_rpm_override(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "paid")
        monkeypatch.setenv("GEMINI_PAID_TIER_RPM", "2000")
        assert get_gemini_rpm() == 2000


class TestSharedGovernanceUsesActiveTierRpm:
    def test_shared_rate_limiter_matches_free_tier_default(self):
        gov = get_shared_governance()
        assert gov.rate_limiter.max_requests == DEFAULT_GEMINI_FREE_TIER_RPM

    def test_shared_rate_limiter_matches_paid_tier_when_configured(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "paid")
        gov = get_shared_governance()
        assert gov.rate_limiter.max_requests == DEFAULT_GEMINI_PAID_TIER_RPM


class TestGeminiRetryCeilingPerTier:
    def test_free_tier_retry_config(self):
        cfg = _gemini_retry_config()
        assert cfg["max_retries"] == 3
        assert cfg["backoff_seconds"] == [5, 15, 45]

    def test_paid_tier_retry_config(self, monkeypatch):
        monkeypatch.setenv("GEMINI_TIER", "paid")
        cfg = _gemini_retry_config()
        assert cfg["max_retries"] == 5
        assert cfg["backoff_seconds"] == [2, 5, 10, 20, 40]

    @patch("time.sleep")
    @patch("requests.post", autospec=True)
    def test_paid_tier_actually_retries_more_times_end_to_end(
        self, mock_post, mock_sleep, monkeypatch,
    ):
        """Persistent 429s all the way through: free tier gives up after 3
        retries (4 total calls), paid tier after 5 (6 total calls)."""
        mock_post.return_value = MagicMock(status_code=429, headers={})

        client_free = GeminiLLMClient(api_key="test-dummy-key")
        with pytest.raises(QuotaExhaustedError):
            client_free.extract({"chunks": [{"raw_text": "free tier call"}]})
        assert mock_post.call_count == 4

        mock_post.reset_mock()
        monkeypatch.setenv("GEMINI_TIER", "paid")
        client_paid = GeminiLLMClient(api_key="test-dummy-key")
        with pytest.raises(QuotaExhaustedError):
            client_paid.extract({"chunks": [{"raw_text": "paid tier call"}]})
        assert mock_post.call_count == 6
