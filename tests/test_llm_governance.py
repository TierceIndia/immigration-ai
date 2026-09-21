"""Tests for LLM governance (Sprint P6)."""
from __future__ import annotations

import time
import pytest
from llm_governance import (
    RateLimiter, RetryPolicy, ResponseCache, BudgetTracker,
    UsageLog, UsageMetrics, LLMGovernance, estimate_cost,
)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------
class TestRateLimiter:
    def test_allows_within_limit(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert rl.is_allowed("user1") is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.is_allowed("user1")
        assert rl.is_allowed("user1") is False

    def test_separate_keys(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        rl.is_allowed("a")
        rl.is_allowed("a")
        assert rl.is_allowed("a") is False
        assert rl.is_allowed("b") is True

    def test_remaining(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        assert rl.remaining("u") == 5
        rl.is_allowed("u")
        assert rl.remaining("u") == 4

    def test_window_expiry(self):
        rl = RateLimiter(max_requests=2, window_seconds=1)
        rl.is_allowed("u")
        rl.is_allowed("u")
        assert rl.is_allowed("u") is False
        time.sleep(1.01)
        assert rl.is_allowed("u") is True


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------
class TestRetryPolicy:
    def test_delay_increases(self):
        rp = RetryPolicy(base_delay=1.0)
        assert rp.delay_for_attempt(0) == 1.0
        assert rp.delay_for_attempt(1) == 2.0
        assert rp.delay_for_attempt(2) == 4.0

    def test_delay_capped(self):
        rp = RetryPolicy(base_delay=10.0, max_delay=15.0)
        assert rp.delay_for_attempt(5) == 15.0


# ---------------------------------------------------------------------------
# ResponseCache
# ---------------------------------------------------------------------------
class TestResponseCache:
    def test_set_and_get(self):
        c = ResponseCache()
        msgs = [{"role": "user", "content": "hi"}]
        c.set("gpt-4o", msgs, "response1")
        assert c.get("gpt-4o", msgs) == "response1"

    def test_miss(self):
        c = ResponseCache()
        assert c.get("gpt-4o", [{"role": "user", "content": "x"}]) is None

    def test_different_model_different_key(self):
        c = ResponseCache()
        msgs = [{"role": "user", "content": "hi"}]
        c.set("gpt-4o", msgs, "r1")
        assert c.get("gpt-4o-mini", msgs) is None

    def test_provider_namespaces_key(self):
        c = ResponseCache()
        msgs = [{"role": "user", "content": "hi"}]
        c.set("gpt-4o", msgs, "openai_response", provider="openai")
        # Same model + messages but different/absent provider -> distinct key.
        assert c.get("gpt-4o", msgs) is None
        assert c.get("gpt-4o", msgs, provider="openai") == "openai_response"
        assert c.get("gpt-4o", msgs, provider="gemini") is None

    def test_expiry(self):
        c = ResponseCache(max_age_seconds=0)
        msgs = [{"role": "user", "content": "hi"}]
        c.set("m", msgs, "r")
        time.sleep(0.01)
        assert c.get("m", msgs) is None

    def test_eviction_oldest(self):
        c = ResponseCache(max_size=2)
        c.set("m", [{"role": "user", "content": "a"}], "ra")
        time.sleep(0.001)
        c.set("m", [{"role": "user", "content": "b"}], "rb")
        time.sleep(0.001)
        c.set("m", [{"role": "user", "content": "c"}], "rc")
        assert len(c) == 2
        assert c.get("m", [{"role": "user", "content": "a"}]) is None

    def test_clear(self):
        c = ResponseCache()
        c.set("m", [{"role": "user", "content": "x"}], "r")
        c.clear()
        assert len(c) == 0


# ---------------------------------------------------------------------------
# BudgetTracker
# ---------------------------------------------------------------------------
class TestBudgetTracker:
    def test_records_usage(self):
        bt = BudgetTracker(daily_limit_usd=10.0, monthly_limit_usd=100.0)
        bt.record("k1", 100, 50, 0.01)
        assert bt.daily_spend("k1") == pytest.approx(0.01)

    def test_over_daily_limit(self):
        bt = BudgetTracker(daily_limit_usd=0.005, monthly_limit_usd=100.0)
        bt.record("k", 1000, 1000, 0.01)
        assert bt.is_over_daily_limit("k") is True

    def test_over_monthly_limit(self):
        bt = BudgetTracker(daily_limit_usd=100.0, monthly_limit_usd=0.005)
        bt.record("k", 1000, 1000, 0.01)
        assert bt.is_over_monthly_limit("k") is True

    def test_not_over_limit(self):
        bt = BudgetTracker(daily_limit_usd=100.0, monthly_limit_usd=100.0)
        bt.record("k", 100, 50, 0.001)
        assert bt.is_over_limit("k") is False

    def test_usage_report(self):
        bt = BudgetTracker(daily_limit_usd=10.0, monthly_limit_usd=100.0)
        bt.record("k", 200, 100, 0.05)
        usage = bt.get_usage("k")
        assert usage["daily"]["cost_usd"] == pytest.approx(0.05)
        assert usage["daily"]["tokens_in"] == 200
        assert usage["daily"]["remaining_usd"] == pytest.approx(9.95)
        assert usage["monthly"]["requests"] == 1


# ---------------------------------------------------------------------------
# UsageLog
# ---------------------------------------------------------------------------
class TestUsageLog:
    def test_record_and_recent(self):
        log = UsageLog(max_entries=100)
        log.record(UsageMetrics(model="gpt-4o", tokens_in=10, tokens_out=5))
        log.record(UsageMetrics(model="gpt-4o", tokens_in=20, tokens_out=10))
        recent = log.recent(1)
        assert len(recent) == 1
        assert recent[0].tokens_in == 20

    def test_totals(self):
        log = UsageLog()
        log.record(UsageMetrics(tokens_in=100, tokens_out=50, cost_usd=0.01))
        log.record(UsageMetrics(tokens_in=200, tokens_out=100, cost_usd=0.02, error="timeout"))
        log.record(UsageMetrics(tokens_in=50, tokens_out=25, cost_usd=0.005, cached=True))
        t = log.totals()
        assert t["requests"] == 3
        assert t["tokens_in"] == 350
        assert t["errors"] == 1
        assert t["cached"] == 1

    def test_eviction(self):
        log = UsageLog(max_entries=3)
        for i in range(5):
            log.record(UsageMetrics(model="m", tokens_in=i))
        assert len(log.recent(10)) == 3
        assert log.recent(1)[0].tokens_in == 4

    def test_clear(self):
        log = UsageLog()
        log.record(UsageMetrics())
        log.clear()
        assert log.totals()["requests"] == 0


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------
class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("gpt-4o", 1000, 1000)
        assert cost == pytest.approx(0.0125)

    def test_unknown_model_uses_default(self):
        cost = estimate_cost("unknown-model", 1000, 1000)
        assert cost > 0


# ---------------------------------------------------------------------------
# LLMGovernance
# ---------------------------------------------------------------------------
class TestLLMGovernance:
    def test_cache_hit_skips_rate_limit(self):
        gov = LLMGovernance(
            rate_limiter=RateLimiter(max_requests=1, window_seconds=60),
            cache=ResponseCache(),
        )
        msgs = [{"role": "user", "content": "hello"}]
        gov.cache.set("gpt-4o", msgs, "cached_response")

        result = gov.pre_request("user1", "gpt-4o", msgs)
        assert result["cached"] is True
        assert result["blocked"] is False

    def test_pre_request_provider_passthrough(self):
        gov = LLMGovernance(cache=ResponseCache())
        msgs = [{"role": "user", "content": "hello"}]
        gov.cache.set("gpt-4o", msgs, "cached_response", provider="openai")
        # Same inputs but a different provider must NOT hit the openai cache entry.
        miss = gov.pre_request("user1", "gpt-4o", msgs, provider="gemini")
        assert miss["cached"] is False
        hit = gov.pre_request("user1", "gpt-4o", msgs, provider="openai")
        assert hit["cached"] is True
        assert hit["cached_response"] == "cached_response"

    def test_rate_limit_blocks(self):
        gov = LLMGovernance(
            rate_limiter=RateLimiter(max_requests=1, window_seconds=60),
            cache=ResponseCache(),
        )
        msgs = [{"role": "user", "content": "hello"}]
        gov.pre_request("user1", "gpt-4o", msgs)  # first request uses the token
        result = gov.pre_request("user1", "gpt-4o", msgs)
        assert result["blocked"] is True
        assert result["reason"] == "rate_limit_exceeded"

    def test_budget_blocks(self):
        gov = LLMGovernance(
            budget=BudgetTracker(daily_limit_usd=0.001, monthly_limit_usd=100.0),
        )
        gov.budget.record("user1", 1000, 1000, 0.01)
        result = gov.pre_request("user1", "gpt-4o", [])
        assert result["blocked"] is True
        assert result["reason"] == "budget_exceeded"

    def test_post_request_updates_budget_and_log(self):
        gov = LLMGovernance()
        entry = gov.post_request(
            "k", "gpt-4o", [{"role": "user", "content": "x"}],
            "response", tokens_in=100, tokens_out=50,
            latency_ms=200.0, retries=0,
        )
        assert entry.cost_usd > 0
        assert gov.budget.daily_spend("k") > 0
        assert gov.usage_log.totals()["requests"] == 1

    def test_post_request_caches_on_success(self):
        gov = LLMGovernance()
        msgs = [{"role": "user", "content": "x"}]
        gov.post_request("k", "gpt-4o", msgs, "ok", 10, 5, 100.0)
        assert gov.cache.get("gpt-4o", msgs) == "ok"

    def test_post_request_no_cache_on_error(self):
        gov = LLMGovernance()
        msgs = [{"role": "user", "content": "x"}]
        gov.post_request("k", "gpt-4o", msgs, None, 10, 5, 100.0, error="fail")
        assert gov.cache.get("gpt-4o", msgs) is None
        assert gov.usage_log.totals()["errors"] == 1
