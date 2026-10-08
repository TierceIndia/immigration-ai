"""Per-host politeness: spacing, jitter, crawl-delay and Retry-After.

Every test here drives a **virtual clock** — no test ever sleeps. The default
process-wide limiter (installed by conftest's autouse fixture) has zero spacing
and a no-op sleep, so the rest of the suite is unaffected by these rules.
"""
from __future__ import annotations

import random
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
from fetch.politeness import (
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MIN_INTERVAL_SECONDS,
    HostLimiter,
    get_limiter,
    jittered_backoff,
    reset_limiter,
    set_limiter,
)
from fetch.browser_fetcher import fetch_with_browser
from fetch.fetcher import FetchResult, FetchStatus
from llm_governance import RetryPolicy


class VirtualClock:
    """Monotonic clock that only advances when something sleeps.

    Thread-safe: concurrent sleeps must not lose increments, or a concurrency
    test could observe an elapsed time lower than the spacing it proved.
    """

    def __init__(self):
        self._now = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += float(seconds)


def make_limiter(clock=None, min_interval=DEFAULT_MIN_INTERVAL_SECONDS,
                 jitter=DEFAULT_JITTER_SECONDS, seed=1234, **kwargs) -> HostLimiter:
    clock = clock or VirtualClock()
    return HostLimiter(
        min_interval=min_interval,
        jitter=jitter,
        clock=clock,
        sleep=clock.sleep,
        rng=random.Random(seed),
        **kwargs,
    ), clock


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_documented_defaults(self):
        assert DEFAULT_MIN_INTERVAL_SECONDS == 8.0
        assert DEFAULT_JITTER_SECONDS == 4.0

    def test_env_overrides_defaults(self, monkeypatch):
        monkeypatch.setenv("FETCH_MIN_INTERVAL_SECONDS", "12.5")
        monkeypatch.setenv("FETCH_JITTER_SECONDS", "0")
        limiter = HostLimiter().use_env()
        assert limiter.min_interval == 12.5
        assert limiter.jitter == 0.0

    def test_garbage_env_falls_back_to_the_more_polite_default(self, monkeypatch):
        monkeypatch.setenv("FETCH_MIN_INTERVAL_SECONDS", "not-a-number")
        limiter = HostLimiter().use_env()
        assert limiter.min_interval == DEFAULT_MIN_INTERVAL_SECONDS

    def test_process_limiter_is_shared_not_rebuilt(self):
        assert get_limiter() is get_limiter()


# ---------------------------------------------------------------------------
# A5 - the headline spacing guarantee
# ---------------------------------------------------------------------------
class TestSameHostSpacing:
    def test_ten_same_host_sources_advance_clock_at_least_nine_intervals(self):
        limiter, clock = make_limiter()
        for i in range(10):
            limiter.wait(f"https://immi.example.gov.au/visa/{i}")
        assert clock() >= 9 * DEFAULT_MIN_INTERVAL_SECONDS

    def test_first_request_to_a_host_is_not_delayed(self):
        limiter, clock = make_limiter()
        limiter.wait("https://fresh.example.gov.au/")
        assert clock() == 0.0

    def test_second_request_pays_at_least_the_minimum_interval(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://host.example.gov.au/a")
        limiter.wait("https://host.example.gov.au/b")
        assert clock() == pytest.approx(DEFAULT_MIN_INTERVAL_SECONDS)

    def test_jitter_stays_within_the_configured_window(self):
        # min_interval=8, jitter=4 -> every gap after the free first request
        # lands in [8, 12].
        limiter, clock = make_limiter()
        limiter.wait("https://host.example.gov.au/0")
        previous = 0.0
        for i in range(1, 21):
            limiter.wait(f"https://host.example.gov.au/{i}")
            elapsed = clock()
            gap = elapsed - previous
            assert DEFAULT_MIN_INTERVAL_SECONDS <= gap <= DEFAULT_MIN_INTERVAL_SECONDS + 4.0
            previous = elapsed

    def test_different_hosts_do_not_delay_each_other(self):
        limiter, clock = make_limiter()
        for host in ("alpha.example.gov.au", "beta.example.gov.au",
                     "gamma.example.gov.au"):
            limiter.wait(f"https://{host}/page")
        assert clock() == 0.0, "independent hosts must not queue behind each other"

    def test_same_host_is_keyed_regardless_of_path(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://host.example.gov.au/one")
        limiter.wait("https://host.example.gov.au/two?x=1#frag")
        assert clock() == pytest.approx(DEFAULT_MIN_INTERVAL_SECONDS)

    def test_host_matching_is_case_insensitive(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://HOST.Example.GOV.AU/one")
        limiter.wait("https://host.example.gov.au/two")
        assert clock() == pytest.approx(DEFAULT_MIN_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# A1 - thread safety (a sync runs on a background thread)
# ---------------------------------------------------------------------------
class TestThreadSafety:
    def test_concurrent_same_host_requests_still_space_out(self):
        limiter, clock = make_limiter(jitter=0.0)
        barrier = threading.Barrier(10)

        def hit():
            barrier.wait()
            limiter.wait("https://busy.example.gov.au/page")

        threads = [threading.Thread(target=hit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert clock() >= 9 * DEFAULT_MIN_INTERVAL_SECONDS

    def test_one_hosts_wait_never_blocks_another_host(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://slow.example.gov.au/")
        # Book a long wait on the slow host, then check a fresh host is free.
        limiter.wait("https://slow.example.gov.au/again")
        before = clock()
        limiter.wait("https://other.example.gov.au/")
        assert clock() == before


# ---------------------------------------------------------------------------
# A2 - robots.txt Crawl-delay
# ---------------------------------------------------------------------------
class TestCrawlDelay:
    def test_larger_crawl_delay_wins_over_our_own_minimum(self):
        limiter, clock = make_limiter(min_interval=8.0, jitter=0.0)
        limiter.note_crawl_delay("https://slow.example.gov.au/", 30)
        limiter.wait("https://slow.example.gov.au/one")
        limiter.wait("https://slow.example.gov.au/two")
        assert clock() == pytest.approx(30.0)

    def test_smaller_crawl_delay_does_not_make_us_less_polite(self):
        # robots asks for 1s; our configured 8s floor still applies.
        limiter, clock = make_limiter(min_interval=8.0, jitter=0.0)
        limiter.note_crawl_delay("https://quick.example.gov.au/", 1)
        limiter.wait("https://quick.example.gov.au/one")
        limiter.wait("https://quick.example.gov.au/two")
        assert clock() == pytest.approx(8.0)

    def test_non_positive_and_missing_values_are_ignored(self):
        limiter, clock = make_limiter()
        limiter.note_crawl_delay("https://h.example.gov.au/", 0)
        limiter.note_crawl_delay("https://h.example.gov.au/", -5)
        assert limiter.crawl_delay_for("https://h.example.gov.au/") == 0.0

    def test_robots_is_consulted_once_per_host_per_run(self):
        limiter, _clock = make_limiter()
        url = "https://once.example.gov.au/page"
        assert limiter.mark_robots_checked(url) is True
        assert limiter.mark_robots_checked(url) is False
        assert limiter.mark_robots_checked(f"{url}/other") is False

    # -- _parse_crawl_delay (pure) --
    def test_parses_star_group(self):
        text = "User-agent: *\nCrawl-delay: 12\nDisallow: /private\n"
        assert fetcher._parse_crawl_delay(text) == 12.0

    def test_parses_our_own_user_agent_group(self):
        text = (
            "User-agent: OtherBot\nCrawl-delay: 1\n\n"
            "User-agent: AU-Immigration-Source-Monitor\nCrawl-delay: 7\n"
        )
        assert fetcher._parse_crawl_delay(text) == 7.0

    def test_ignores_groups_that_are_not_ours(self):
        text = "User-agent: OtherBot\nCrawl-delay: 99\n"
        assert fetcher._parse_crawl_delay(text) is None

    def test_uses_the_max_across_applicable_groups(self):
        text = (
            "User-agent: *\nCrawl-delay: 5\n\n"
            "User-agent: AU-Immigration-Source-Monitor\nCrawl-delay: 20\n"
        )
        assert fetcher._parse_crawl_delay(text) == 20.0

    def test_returns_none_when_absent_or_malformed(self):
        assert fetcher._parse_crawl_delay("User-agent: *\nDisallow: /x\n") is None
        assert fetcher._parse_crawl_delay("User-agent: *\nCrawl-delay: soon\n") is None
        assert fetcher._parse_crawl_delay("") is None

    def test_comments_are_stripped_before_parsing(self):
        text = "User-agent: *  # robots for everyone\nCrawl-delay: 9 # seconds\n"
        assert fetcher._parse_crawl_delay(text) == 9.0


# ---------------------------------------------------------------------------
# A2 - Retry-After on 429/503
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code=200, headers=None, text="", content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = content


class TestRetryAfter:
    @pytest.mark.parametrize("status", [429, 503])
    def test_retry_after_pauses_that_host(self, status):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://busy.example.gov.au/")
        fetcher._honour_retry_after(
            _FakeResponse(status_code=status, headers={"Retry-After": "45"}),
            "https://busy.example.gov.au/page", limiter,
        )
        # Next request to that host must be pushed out by the server's say-so.
        limiter.wait("https://busy.example.gov.au/again")
        assert clock() >= 45.0

    def test_other_hosts_are_unaffected_by_retry_after(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://busy.example.gov.au/")
        fetcher._honour_retry_after(
            _FakeResponse(status_code=429, headers={"Retry-After": "600"}),
            "https://busy.example.gov.au/page", limiter,
        )
        limiter.wait("https://calm.example.gov.au/")
        assert clock() == 0.0

    @pytest.mark.parametrize("status", [200, 403])
    def test_retry_after_is_only_read_from_429_and_503(self, status):
        limiter, clock = make_limiter()
        fetcher._honour_retry_after(
            _FakeResponse(status_code=status, headers={"Retry-After": "600"}),
            "https://h.example.gov.au/", limiter,
        )
        limiter.wait("https://h.example.gov.au/")
        assert clock() == 0.0

    def test_http_date_form_is_ignored_not_guessed(self):
        limiter, clock = make_limiter()
        fetcher._honour_retry_after(
            _FakeResponse(status_code=503,
                          headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            "https://h.example.gov.au/", limiter,
        )
        limiter.wait("https://h.example.gov.au/")
        assert clock() == 0.0

    def test_missing_header_is_a_no_op(self):
        limiter, clock = make_limiter()
        fetcher._honour_retry_after(
            _FakeResponse(status_code=429, headers={}),
            "https://h.example.gov.au/", limiter,
        )
        limiter.wait("https://h.example.gov.au/")
        assert clock() == 0.0


# ---------------------------------------------------------------------------
# A3 - the browser path must actually call the limiter
#
# The old code defined _polite_delay()/MIN_BROWSER_DELAY_SECONDS and never
# called them; nothing in the suite noticed. These tests fail if the browser
# path stops being spaced.
# ---------------------------------------------------------------------------
def _ok(url: str) -> FetchResult:
    return FetchResult(url=url, status=FetchStatus.OK, http_status=200,
                       content_type="text/html", raw_bytes=b"<html>x</html>",
                       duration_ms=1, error=None)


class _RecordingLimiter:
    def __init__(self):
        self.calls = []

    def wait(self, url_or_host):
        self.calls.append(url_or_host)
        return 0.0


class TestBrowserPathIsPolite:
    def test_browser_fetch_calls_the_limiter(self):
        recorder = _RecordingLimiter()
        fetch_with_browser(
            "https://example.gov.au/page", "HTML", 10,
            launch_fn=lambda url, st, ts: _ok(url),
            limiter=recorder,
        )
        assert recorder.calls == ["https://example.gov.au/page"]

    def test_browser_fetch_defaults_to_the_process_limiter(self):
        recorder = _RecordingLimiter()
        set_limiter(recorder)
        fetch_with_browser(
            "https://example.gov.au/page", "HTML", 10,
            launch_fn=lambda url, st, ts: _ok(url),
        )
        assert recorder.calls == ["https://example.gov.au/page"]

    def test_two_browser_fetches_to_one_host_are_spaced(self):
        """The regression test the dead _polite_delay() never had."""
        limiter, clock = make_limiter(jitter=0.0)
        set_limiter(limiter)
        for i in range(3):
            fetch_with_browser(
                f"https://spaced.example.gov.au/{i}", "HTML", 10,
                launch_fn=lambda url, st, ts: _ok(url),
            )
        assert clock() >= 2 * DEFAULT_MIN_INTERVAL_SECONDS

    def test_browser_and_plain_fetches_share_one_limiter(self, monkeypatch):
        limiter, clock = make_limiter(jitter=0.0)
        set_limiter(limiter)

        fetched = []

        def fake_get(url, **kwargs):
            fetched.append(url)
            return _FakeResponse(
                status_code=200, content=b"y" * 60, text="y" * 60,
                headers={"Content-Type": "text/html"},
            )

        monkeypatch.setattr(fetcher.requests, "get", fake_get)

        # First request of the run: no wait, and it reserves this host's slot.
        fetch_with_browser(
            "https://shared.example.gov.au/b", "HTML", 10,
            launch_fn=lambda url, st, ts: _ok(url),
        )
        assert clock() == 0.0

        # The plain path must queue behind that slot rather than fire at once.
        fetcher.fetch("https://shared.example.gov.au/plain")
        assert clock() >= DEFAULT_MIN_INTERVAL_SECONDS
        assert "https://shared.example.gov.au/plain" in fetched


# ---------------------------------------------------------------------------
# A4 - jitter on retry backoff
# ---------------------------------------------------------------------------
class _ExplodingRng:
    """Fails the test if anything draws from the RNG when jitter is off."""

    def uniform(self, a, b):  # pragma: no cover - must never run
        raise AssertionError("RNG was drawn from although jitter was disabled")


class TestBackoffJitter:
    def test_jitter_off_is_exactly_deterministic(self):
        assert jittered_backoff(4.0, 0.0, _ExplodingRng()) == 4.0
        assert jittered_backoff(4.0, -1.0, _ExplodingRng()) == 4.0
        assert jittered_backoff(0.0, 4.0, _ExplodingRng()) == 0.0

    def test_jitter_widens_the_window_downwards_only(self):
        rng = random.Random(7)
        for _ in range(200):
            value = jittered_backoff(6.0, 4.0, rng)
            assert 2.0 <= value <= 6.0, "window must be [base-jitter, base]"

    def test_jitter_at_least_base_is_full_jitter(self):
        rng = random.Random(11)
        seen = {jittered_backoff(4.0, 4.0, rng) for _ in range(300)}
        assert min(seen) < 1.0, "full jitter should reach near zero"
        assert max(seen) <= 4.0

    def test_backoff_method_returns_what_it_slept(self):
        slept = []
        limiter = HostLimiter(min_interval=0.0, jitter=0.0,
                              sleep=slept.append, clock=lambda: 0.0)
        assert limiter.backoff(3.0) == 3.0
        assert slept == [3.0]

    def test_limiter_backoff_with_jitter_stays_in_window(self):
        slept = []
        limiter = HostLimiter(min_interval=0.0, jitter=4.0, rng=random.Random(3),
                              sleep=slept.append, clock=lambda: 0.0)
        for _ in range(50):
            value = limiter.backoff(6.0)
            assert 2.0 <= value <= 6.0
        assert len(slept) == 50


class TestLlmRetryPolicyJitter:
    def test_bare_policy_stays_exactly_deterministic(self):
        policy = RetryPolicy(base_delay=1.0)
        assert [policy.delay_for_attempt(i) for i in range(3)] == [1.0, 2.0, 4.0]

    def test_bare_policy_never_draws_from_an_rng(self):
        policy = RetryPolicy(base_delay=1.0, rng=_ExplodingRng())
        assert policy.delay_for_attempt(2) == 4.0

    def test_jittered_policy_stays_within_the_window(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=30.0, jitter=4.0,
                             rng=random.Random(5))
        for attempt in range(4):
            base = min(1.0 * (2 ** attempt), 30.0)
            value = policy.delay_for_attempt(attempt)
            assert max(0.0, base - 4.0) <= value <= base

    def test_jitter_is_capped_by_max_delay_before_widening(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=15.0, jitter=0.0)
        assert policy.delay_for_attempt(50) == 15.0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
class TestLimiterReset:
    def test_reset_forgets_everything(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.note_crawl_delay("https://h.example.gov.au/", 30)
        limiter.mark_robots_checked("https://h.example.gov.au/")
        limiter.wait("https://h.example.gov.au/")
        assert clock() == 0.0

        limiter.reset()
        assert limiter.crawl_delay_for("https://h.example.gov.au/") == 0.0
        assert limiter.mark_robots_checked("https://h.example.gov.au/") is True
        limiter.wait("https://h.example.gov.au/")
        limiter.wait("https://h.example.gov.au/")
        assert clock() == pytest.approx(8.0)

    def test_reset_for_one_host_leaves_others_alone(self):
        limiter, clock = make_limiter(jitter=0.0)
        limiter.wait("https://a.example.gov.au/")
        limiter.wait("https://b.example.gov.au/")
        limiter.reset("https://a.example.gov.au/")
        limiter.wait("https://a.example.gov.au/")
        after_a = clock()
        limiter.wait("https://a.example.gov.au/")
        assert clock() == after_a + 8.0

    def test_set_limiter_none_restores_lazy_construction(self):
        set_limiter(None)
        first = get_limiter()
        assert first is get_limiter()
        reset_limiter()
