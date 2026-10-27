"""In-process login throttling.

One API process gets a bounded failed-login window per (client IP, email).
This is deliberately not a distributed lock: a multi-worker deployment needs a
shared store, but a silent no-op would be worse than an explicitly process-local
control. The limits are env-configurable so an operator can tune them without a
code change.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


@dataclass
class _Entry:
    failures: int
    first_failure_at: float
    locked_until: float


_LOCK = threading.Lock()
_ENTRIES: dict[str, _Entry] = {}
_MAX_ENTRIES = 10_000


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, 86_400))


def _settings() -> tuple[int, float, float]:
    max_attempts = _int_env("LOGIN_MAX_ATTEMPTS", 5)
    window = _int_env("LOGIN_WINDOW_SECONDS", 300)
    lockout = _int_env("LOGIN_LOCKOUT_SECONDS", 900)
    return max_attempts, float(window), float(lockout)


def _prune(now: float) -> None:
    if len(_ENTRIES) <= _MAX_ENTRIES:
        return
    expired = [
        key for key, entry in _ENTRIES.items()
        if entry.locked_until <= now
        and now - entry.first_failure_at >= _settings()[1]
    ]
    for key in expired:
        _ENTRIES.pop(key, None)
    if len(_ENTRIES) > _MAX_ENTRIES:
        # Hard cap: drop the oldest entries rather than grow without bound.
        for key in sorted(
            _ENTRIES, key=lambda k: _ENTRIES[k].first_failure_at
        )[:_MAX_ENTRIES // 2]:
            _ENTRIES.pop(key, None)


def retry_after_seconds(key: str) -> int:
    """Seconds until this key may try again, or 0 when it is not locked."""
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(key)
        if not entry:
            return 0
        if entry.locked_until > now:
            return max(1, int(entry.locked_until - now + 0.999))
        window = _settings()[1]
        if now - entry.first_failure_at >= window:
            _ENTRIES.pop(key, None)
        return 0


def record_failure(key: str) -> int:
    """Record one failed attempt; return lockout seconds if it just locked."""
    max_attempts, window, lockout = _settings()
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None or now - entry.first_failure_at >= window:
            entry = _Entry(failures=0, first_failure_at=now, locked_until=0.0)
            _ENTRIES[key] = entry
        entry.failures += 1
        if entry.failures >= max_attempts:
            entry.locked_until = now + lockout
            return max(1, int(lockout))
        _prune(now)
        return 0


def reset(key: str) -> None:
    with _LOCK:
        _ENTRIES.pop(key, None)


def reset_all() -> None:
    """Test helper; never called by production code."""
    with _LOCK:
        _ENTRIES.clear()
