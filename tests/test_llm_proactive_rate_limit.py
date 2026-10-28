"""Regression tests for Part 2b of the Gemini free-tier quota fix: proactive
rate limiting.

Before this fix, RateLimiter only reacted to an already-exceeded limit by
rejecting the next call outright (is_allowed() -> False), so a normal sync
run would fire calls immediately and only find out it overshot Gemini's
free-tier RPM when the provider itself returned a 429. RateLimiter now also
supports waiting proactively for a free slot (wait_and_allow()), and
GeminiLLMClient opts into this via LLMGovernance.pre_request(..., wait=True),
so a normal sync should rarely hit a real 429 at all.

The clock is faked (time_fn/sleep_fn) so this test proves the waiting logic
without a real sleep.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_governance import LLMGovernance, RateLimiter, ResponseCache, UsageLog
from extraction.llm_client import GeminiLLMClient


class _FakeClock:
    """A controllable clock: advances only when sleep() is called."""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class TestRateLimiterWaitAndAllow:
    def test_wait_and_allow_blocks_until_a_slot_is_free(self):
        clock = _FakeClock()
        rl = RateLimiter(max_requests=2, window_seconds=60,
                          time_fn=clock.time, sleep_fn=clock.sleep)
        assert rl.wait_and_allow("k") == 0.0
        assert rl.wait_and_allow("k") == 0.0
        # Third call in the same window must wait out the first call's window.
        waited = rl.wait_and_allow("k")
        assert waited == pytest.approx(60.0)
        assert clock.slept == [60.0]

    def test_wait_and_allow_never_waits_when_under_the_limit(self):
        clock = _FakeClock()
        rl = RateLimiter(max_requests=100, window_seconds=60,
                          time_fn=clock.time, sleep_fn=clock.sleep)
        for _ in range(10):
            assert rl.wait_and_allow("k") == 0.0
        assert clock.slept == []

    def test_is_allowed_still_rejects_reactively_and_never_sleeps(self):
        """The old reactive API (is_allowed) must be completely unaffected -
        existing callers that rely on immediate rejection keep working."""
        clock = _FakeClock()
        rl = RateLimiter(max_requests=1, window_seconds=60,
                          time_fn=clock.time, sleep_fn=clock.sleep)
        assert rl.is_allowed("k") is True
        assert rl.is_allowed("k") is False
        assert clock.slept == []


class TestGovernancePreRequestProactiveWait:
    def test_wait_true_waits_instead_of_blocking(self):
        clock = _FakeClock()
        gov = LLMGovernance(
            rate_limiter=RateLimiter(max_requests=1, window_seconds=60,
                                     time_fn=clock.time, sleep_fn=clock.sleep),
            cache=ResponseCache(),
        )
        msgs = [{"role": "user", "content": "hello"}]
        gov.pre_request("user1", "gemini-flash-lite-latest", msgs, wait=True)
        result = gov.pre_request("user1", "gemini-flash-lite-latest", msgs, wait=True)
        assert result["blocked"] is False
        assert result["waited_seconds"] == pytest.approx(60.0)
        assert clock.slept == [60.0]

    def test_wait_false_still_blocks_immediately_default_behavior(self):
        """Default (wait=False) behavior from before this fix is unchanged."""
        gov = LLMGovernance(
            rate_limiter=RateLimiter(max_requests=1, window_seconds=60),
            cache=ResponseCache(),
        )
        msgs = [{"role": "user", "content": "hello"}]
        gov.pre_request("user1", "gpt-4o", msgs)
        result = gov.pre_request("user1", "gpt-4o", msgs)
        assert result["blocked"] is True
        assert result["reason"] == "rate_limit_exceeded"


class TestGeminiClientProactiveWaitEndToEnd:
    @patch("time.sleep")
    @patch("requests.post", autospec=True)
    def test_many_calls_within_the_rpm_limit_never_raise_rate_limit_exceeded(
        self, mock_post, mock_time_sleep,
    ):
        """With a tiny RPM cap and a fake rate-limiter clock, firing more
        extractions than the per-minute cap allows must wait (not error) -
        the real-world behavior this fix is for: a sync shouldn't abort a
        chunk just because several others were extracted moments earlier."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }
        mock_post.return_value = resp

        clock = _FakeClock()
        gov = LLMGovernance(
            rate_limiter=RateLimiter(max_requests=2, window_seconds=60,
                                     time_fn=clock.time, sleep_fn=clock.sleep),
            usage_log=UsageLog(),
        )
        client = GeminiLLMClient(api_key="test-dummy-key", governance=gov)

        for i in range(5):
            result = client.extract({"chunks": [{"raw_text": f"distinct chunk {i}"}]})
            assert result == '{"ok": true}'

        # 5 distinct (uncached) calls against a 2-per-minute cap must have
        # waited twice (every 3rd call onward within the same window), never
        # raised, and never touched the real clock.
        assert mock_post.call_count == 5
        assert len(clock.slept) == 2
        mock_time_sleep.assert_not_called()
