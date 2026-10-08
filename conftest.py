"""Root conftest - adds src/ to sys.path for all test modules."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pytest

from fetch.politeness import HostLimiter, reset_limiter, set_limiter


@pytest.fixture(autouse=True)
def politeness_for_tests():
    """Tests must never sleep for politeness' sake.

    Installs a process-wide limiter with zero spacing, zero jitter and a no-op
    sleep, so a test that fetches several URLs on one host is not delayed by
    FETCH_MIN_INTERVAL_SECONDS. Every test that actually exercises spacing
    builds its own HostLimiter with a virtual clock (see tests/test_politeness.py)
    and installs it with set_limiter().

    Also guarantees each test starts from a clean limiter: no remembered last
    request, crawl-delay, Retry-After or robots.txt lookup from a previous test.
    """
    set_limiter(
        HostLimiter(min_interval=0.0, jitter=0.0, sleep=lambda seconds: None)
    )
    yield
    reset_limiter()
