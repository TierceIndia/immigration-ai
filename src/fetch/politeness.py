"""Per-host politeness for outbound retrieval.

Background (why this module exists): one sync run issued ~10 requests to a single
government host in about 3.4 seconds (~3 req/s). Nothing in the fetch path spaced
requests at all — ``browser_fetcher._polite_delay()`` existed but was never called,
so there was neither a minimum interval nor any jitter anywhere in the codebase.

This module owns those two knobs and nothing else:

* ``FETCH_MIN_INTERVAL_SECONDS`` (default 8) — minimum spacing between two requests
  to the **same host**.
* ``FETCH_JITTER_SECONDS`` (default 4) — additive jitter drawn from
  ``uniform(0, FETCH_JITTER_SECONDS)`` so a sync does not develop a metronomic
  pattern that looks like a bot.

The limiter is **per host**: two different hosts never delay each other. It is
shared process-wide for a whole sync run (a sync runs on a background thread, so
every mutation is guarded by one lock) and is used by *both* retrieval paths —
``fetcher.fetch()`` (plain HTTP) and ``browser_fetcher.fetch_with_browser()``
(Patchright).

All three non-deterministic inputs — clock, sleep and RNG — are injectable, so
tests drive a virtual clock and never actually sleep.

This is ordinary client-side etiquette, not evasion: it changes *when* we ask,
never *how* we identify ourselves (``fetcher.USER_AGENT`` is fixed) and never
which IP we ask from. See ``fetcher.py``'s module docstring for the no-evasion
policy this module is deliberately silent about.
"""
from __future__ import annotations

import os
import random
import threading
import time
from urllib.parse import urlparse

DEFAULT_MIN_INTERVAL_SECONDS = 8.0
DEFAULT_JITTER_SECONDS = 4.0


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on absence/garbage.

    Falling back to the *more polite* default is intentional: a malformed
    ``FETCH_MIN_INTERVAL_SECONDS`` must never silently turn into a faster,
    less polite crawl.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    return value if value >= 0 else float(default)


def host_of(url: str) -> str:
    """Lowercased ``host[:port]`` of ``url`` — the limiter's key."""
    return urlparse(url).netloc.lower()


def jittered_backoff(base_delay: float, jitter: float, rng) -> float:
    """Retry backoff with jitter.

    With ``jitter <= 0`` this returns ``base_delay`` untouched and does not draw
    from ``rng`` at all, so a jitter-off run is byte-for-byte the legacy
    deterministic behaviour. With ``jitter >= base_delay`` it degenerates to
    classic *full jitter* (``uniform(0, base_delay)``); in between it widens the
    window downwards from ``base_delay``.

    Named ``jittered_backoff`` rather than ``full_jitter`` because the exact
    shape depends on ``jitter``, which is a configured duration, not a boolean.
    """
    if base_delay <= 0 or jitter <= 0:
        return float(base_delay)
    low = max(0.0, float(base_delay) - float(jitter))
    return rng.uniform(low, float(base_delay))


class HostLimiter:
    """Spaces requests to one host; different hosts are independent.

    ``clock``/``sleep``/``rng`` are injectable seams so tests can drive a
    virtual clock (no real sleeping) and a seeded RNG (no flakiness).
    """

    def __init__(
        self,
        min_interval: float | None = None,
        jitter: float | None = None,
        clock=None,
        sleep=None,
        rng=None,
    ):
        self.min_interval = (
            DEFAULT_MIN_INTERVAL_SECONDS if min_interval is None
            else float(min_interval)
        )
        self.jitter = (
            DEFAULT_JITTER_SECONDS if jitter is None else float(jitter)
        )
        self._clock = clock if clock is not None else time.monotonic
        # Public: retry backoff in fetcher.py sleeps through the same seam, so a
        # test that injects a no-op sleep is never delayed by either mechanism.
        self.sleep = sleep if sleep is not None else time.sleep
        # Public: the retry paths draw from this exact RNG (see jittered_backoff).
        self.rng = rng if rng is not None else random.Random()
        self._lock = threading.Lock()
        # Absolute monotonic timestamps, keyed by lowercased host.
        self._last_request: dict[str, float] = {}
        # Per-host effective interval = max(min_interval, crawl-delay seen).
        self._crawl_delay: dict[str, float] = {}
        # Per-host "do not talk to me before" watermark from Retry-After.
        self._blocked_until: dict[str, float] = {}
        # Hosts whose robots.txt has already been consulted this run.
        self._robots_checked: set[str] = set()

    # -- configuration -----------------------------------------------------
    def use_env(self) -> "HostLimiter":
        """Apply ``FETCH_MIN_INTERVAL_SECONDS`` / ``FETCH_JITTER_SECONDS``.

        Returns ``self`` for chaining. Called by :func:`get_limiter` when the
        process-wide limiter is first constructed, so the values are read once
        per process rather than per request.
        """
        self.min_interval = _env_float(
            "FETCH_MIN_INTERVAL_SECONDS", DEFAULT_MIN_INTERVAL_SECONDS
        )
        self.jitter = _env_float("FETCH_JITTER_SECONDS", DEFAULT_JITTER_SECONDS)
        return self

    # -- waiting -----------------------------------------------------------
    def wait(self, url_or_host: str) -> float:
        """Block until it is this host's turn. Returns the seconds actually waited.

        The slot is reserved *under the lock* and slept *outside* it, so a
        thread waiting on host A never blocks a thread waiting on host B, and
        concurrent requests to the same host queue into successive slots rather
        than collapsing onto the same instant.
        """
        host = self._normalise(url_or_host)
        with self._lock:
            now = self._clock()
            interval = max(
                self.min_interval, self._crawl_delay.get(host, 0.0)
            )
            last = self._last_request.get(host)
            if last is None:
                target = now
            else:
                target = last + interval
                if self.jitter > 0:
                    target += self.rng.uniform(0.0, self.jitter)
            target = max(target, self._blocked_until.get(host, 0.0))
            delay = target - now
            if delay <= 0:
                delay = 0.0
                target = now
            self._last_request[host] = target
        if delay > 0:
            self.sleep(delay)
        return delay

    def backoff(self, base_delay: float) -> float:
        """Sleep a jittered retry backoff and return the seconds slept.

        With jitter off this sleeps exactly ``base_delay`` and draws nothing
        from the RNG, i.e. the pre-existing deterministic behaviour.
        """
        delay = jittered_backoff(base_delay, self.jitter, self.rng)
        if delay > 0:
            self.sleep(delay)
        return delay

    def mark_robots_checked(self, url_or_host: str) -> bool:
        """Atomically claim the single robots.txt lookup for this host.

        True the first time for a host (caller should fetch robots.txt); False
        for every later call, so a sync consults each host's robots.txt exactly
        once and a failing lookup is never retried in a loop.
        """
        host = self._normalise(url_or_host)
        with self._lock:
            if host in self._robots_checked:
                return False
            self._robots_checked.add(host)
            return True

    # -- inputs the policy honours ----------------------------------------
    def note_crawl_delay(self, url_or_host: str, seconds: float) -> None:
        """Record a robots.txt ``Crawl-delay`` for this host (the larger wins)."""
        if seconds is None or seconds <= 0:
            return
        host = self._normalise(url_or_host)
        with self._lock:
            self._crawl_delay[host] = max(
                self._crawl_delay.get(host, 0.0), float(seconds)
            )

    def note_retry_after(self, url_or_host: str, seconds: float) -> None:
        """Honour a ``Retry-After`` from a 429/503 by pausing this host."""
        if seconds is None or seconds <= 0:
            return
        host = self._normalise(url_or_host)
        with self._lock:
            until = self._clock() + float(seconds)
            self._blocked_until[host] = max(
                self._blocked_until.get(host, 0.0), until
            )

    # -- introspection (tests / diagnostics) -------------------------------
    def next_allowed_at(self, url_or_host: str) -> float | None:
        """Monotonic timestamp of this host's reserved slot, if one exists."""
        with self._lock:
            return self._last_request.get(self._normalise(url_or_host))

    def crawl_delay_for(self, url_or_host: str) -> float:
        with self._lock:
            return self._crawl_delay.get(self._normalise(url_or_host), 0.0)

    def reset(self, url_or_host: str | None = None) -> None:
        """Forget one host (or, with ``None``, every host)."""
        with self._lock:
            if url_or_host is None:
                self._last_request.clear()
                self._crawl_delay.clear()
                self._blocked_until.clear()
                self._robots_checked.clear()
                return
            host = self._normalise(url_or_host)
            self._last_request.pop(host, None)
            self._crawl_delay.pop(host, None)
            self._blocked_until.pop(host, None)
            self._robots_checked.discard(host)

    @staticmethod
    def _normalise(url_or_host: str) -> str:
        """Accept either a full URL or a bare host and return the host key."""
        text = (url_or_host or "").strip().lower()
        if "://" in text:
            return urlparse(text).netloc.lower()
        return text


# ---------------------------------------------------------------------------
# Process-wide instance: one sync run shares one limiter, and both retrieval
# paths reach for it here.
# ---------------------------------------------------------------------------
_limiter: HostLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> HostLimiter:
    """The process-wide limiter, constructed (and env-configured) on first use."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = HostLimiter().use_env()
        return _limiter


def set_limiter(limiter: HostLimiter | None) -> None:
    """Install a limiter (tests inject a virtual-clock one) or clear it."""
    global _limiter
    with _limiter_lock:
        _limiter = limiter


def reset_limiter() -> None:
    """Drop the process-wide limiter so the next ``get_limiter()`` re-reads env."""
    set_limiter(None)
