"""LLM governance — rate limits, retry, cache, budget, usage tracking."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
@dataclass
class RateLimiter:
    """Token-bucket rate limiter per key (user_id or API key)."""
    max_requests: int = 60
    window_seconds: int = 60

    def __post_init__(self):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        if len(self._requests[key]) >= self.max_requests:
            return False
        self._requests[key].append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        recent = [t for t in self._requests[key] if t > cutoff]
        return max(0, self.max_requests - len(recent))


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------
@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_exceptions: tuple = (TimeoutError, ConnectionError, OSError)

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)


# ---------------------------------------------------------------------------
# Response Cache
# ---------------------------------------------------------------------------
class ResponseCache:
    """LRU-ish cache keyed by prompt hash. Evicts entries older than max_age."""

    def __init__(self, max_size: int = 1000, max_age_seconds: int = 3600):
        self.max_size = max_size
        self.max_age = max_age_seconds
        self._cache: dict[str, tuple[Any, float]] = {}

    @staticmethod
    def _key(model: str, messages: list[dict],
             prompt_version: str = "", schema_version: str = "",
             temperature: float = 0.0) -> str:
        raw = json.dumps({
            "model": model,
            "messages": messages,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "temperature": temperature,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, model: str, messages: list[dict],
            prompt_version: str = "", schema_version: str = "",
            temperature: float = 0.0) -> Any | None:
        key = self._key(model, messages, prompt_version, schema_version, temperature)
        if key in self._cache:
            value, ts = self._cache[key]
            if time.monotonic() - ts < self.max_age:
                return value
            del self._cache[key]
        return None

    def set(self, model: str, messages: list[dict], value: Any,
            prompt_version: str = "", schema_version: str = "",
            temperature: float = 0.0) -> None:
        key = self._key(model, messages, prompt_version, schema_version, temperature)
        if len(self._cache) >= self.max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        self._cache[key] = (value, time.monotonic())

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Budget Tracker
# ---------------------------------------------------------------------------
@dataclass
class BudgetEntry:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    requests: int = 0


class BudgetTracker:
    """Tracks usage per key with daily and monthly limits."""

    def __init__(self, daily_limit_usd: float = 10.0,
                 monthly_limit_usd: float = 200.0):
        self.daily_limit = daily_limit_usd
        self.monthly_limit = monthly_limit_usd
        self._daily: dict[str, dict[str, BudgetEntry]] = defaultdict(lambda: defaultdict(BudgetEntry))
        self._monthly: dict[str, dict[str, BudgetEntry]] = defaultdict(lambda: defaultdict(BudgetEntry))

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def record(self, key: str, tokens_in: int, tokens_out: int,
               cost_usd: float) -> None:
        day = self._today()
        month = self._month()
        entry_d = self._daily[key][day]
        entry_d.tokens_in += tokens_in
        entry_d.tokens_out += tokens_out
        entry_d.cost_usd += cost_usd
        entry_d.requests += 1

        entry_m = self._monthly[key][month]
        entry_m.tokens_in += tokens_in
        entry_m.tokens_out += tokens_out
        entry_m.cost_usd += cost_usd
        entry_m.requests += 1

    def daily_spend(self, key: str) -> float:
        day = self._today()
        return self._daily[key][day].cost_usd

    def monthly_spend(self, key: str) -> float:
        month = self._month()
        return self._monthly[key][month].cost_usd

    def is_over_daily_limit(self, key: str) -> bool:
        return self.daily_spend(key) >= self.daily_limit

    def is_over_monthly_limit(self, key: str) -> bool:
        return self.monthly_spend(key) >= self.monthly_limit

    def is_over_limit(self, key: str) -> bool:
        return self.is_over_daily_limit(key) or self.is_over_monthly_limit(key)

    def get_usage(self, key: str) -> dict:
        day = self._today()
        month = self._month()
        d = self._daily[key][day]
        m = self._monthly[key][month]
        return {
            "daily": {
                "cost_usd": round(d.cost_usd, 6),
                "tokens_in": d.tokens_in,
                "tokens_out": d.tokens_out,
                "requests": d.requests,
                "limit_usd": self.daily_limit,
                "remaining_usd": round(max(0, self.daily_limit - d.cost_usd), 6),
            },
            "monthly": {
                "cost_usd": round(m.cost_usd, 6),
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "requests": m.requests,
                "limit_usd": self.monthly_limit,
                "remaining_usd": round(max(0, self.monthly_limit - m.cost_usd), 6),
            },
        }


# ---------------------------------------------------------------------------
# Usage Metrics Logger
# ---------------------------------------------------------------------------
@dataclass
class UsageMetrics:
    """Collects per-request metrics for observability."""
    request_id: str = ""
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False
    retries: int = 0
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class UsageLog:
    """In-memory log of usage metrics. Replace with DB-backed store in production."""

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self._entries: list[UsageMetrics] = []

    def record(self, entry: UsageMetrics) -> None:
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def recent(self, n: int = 50) -> list[UsageMetrics]:
        return self._entries[-n:]

    def totals(self) -> dict:
        if not self._entries:
            return {"requests": 0, "tokens_in": 0, "tokens_out": 0,
                    "cost_usd": 0.0, "cached": 0, "errors": 0}
        return {
            "requests": len(self._entries),
            "tokens_in": sum(e.tokens_in for e in self._entries),
            "tokens_out": sum(e.tokens_out for e in self._entries),
            "cost_usd": round(sum(e.cost_usd for e in self._entries), 6),
            "cached": sum(1 for e in self._entries if e.cached),
            "errors": sum(1 for e in self._entries if e.error),
        }

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Price config (USD per 1K tokens) — override via env or config
# ---------------------------------------------------------------------------
DEFAULT_PRICES = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int,
                  prices: dict | None = None) -> float:
    """Estimate USD cost for a request."""
    p = (prices or DEFAULT_PRICES).get(model, {"input": 0.005, "output": 0.015})
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1000


# ---------------------------------------------------------------------------
# Governance Manager — ties everything together
# ---------------------------------------------------------------------------
class LLMGovernance:
    """Facade that applies rate limits, caching, retry, budget checks."""

    def __init__(self, *, rate_limiter: RateLimiter | None = None,
                 retry_policy: RetryPolicy | None = None,
                 cache: ResponseCache | None = None,
                 budget: BudgetTracker | None = None,
                 usage_log: UsageLog | None = None):
        self.rate_limiter = rate_limiter or RateLimiter()
        self.retry_policy = retry_policy or RetryPolicy()
        self.cache = cache or ResponseCache()
        self.budget = budget or BudgetTracker()
        self.usage_log = usage_log or UsageLog()

    def pre_request(self, key: str, model: str,
                    messages: list[dict],
                    prompt_version: str = "",
                    schema_version: str = "",
                    temperature: float = 0.0) -> dict[str, Any]:
        """Check cache, rate limits, budget before making an LLM call.
        Returns metadata dict with {cached, cached_response, blocked, reason}.
        """
        # 1. Cache lookup
        cached = self.cache.get(model, messages, prompt_version, schema_version, temperature)
        if cached is not None:
            return {"cached": True, "cached_response": cached,
                    "blocked": False, "reason": None}

        # 2. Rate limit
        if not self.rate_limiter.is_allowed(key):
            return {"cached": False, "cached_response": None,
                    "blocked": True, "reason": "rate_limit_exceeded"}

        # 3. Budget
        if self.budget.is_over_limit(key):
            return {"cached": False, "cached_response": None,
                    "blocked": True, "reason": "budget_exceeded"}

        return {"cached": False, "cached_response": None,
                "blocked": False, "reason": None}

    def post_request(self, key: str, model: str,
                     messages: list[dict], response: Any,
                     tokens_in: int, tokens_out: int,
                     latency_ms: float, retries: int = 0,
                     error: str | None = None,
                     prompt_version: str = "",
                     schema_version: str = "",
                     temperature: float = 0.0) -> UsageMetrics:
        """Record usage, update cache and budget after an LLM call."""
        cost = estimate_cost(model, tokens_in, tokens_out)
        self.budget.record(key, tokens_in, tokens_out, cost)

        if error is None:
            self.cache.set(model, messages, response, prompt_version, schema_version, temperature)

        entry = UsageMetrics(
            model=model, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost, latency_ms=latency_ms, retries=retries,
            error=error,
        )
        self.usage_log.record(entry)

        logger.info("LLM request completed",
                     extra={"model": model, "tokens_in": tokens_in,
                            "tokens_out": tokens_out, "cost_usd": cost,
                            "latency_ms": latency_ms, "cached": False,
                            "retries": retries, "error": error})
        return entry
