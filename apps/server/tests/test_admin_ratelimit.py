"""Tests for the process-level login limiter + PBKDF2 concurrency bound (F1-11, RF-18).

These lock the two defense-in-depth mechanisms: failures accumulate PER KEY at the process level
(so a new session cannot reset the budget), and simultaneous PBKDF2 verifications are capped.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from aethercal.server.admin.ratelimit import (
    LoginRateLimiter,
    LoginThrottledError,
    Pbkdf2Limiter,
)


class _FakeClock:
    """A manually advanced monotonic clock for deterministic window/lockout tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: _FakeClock) -> LoginRateLimiter:
    return LoginRateLimiter(
        max_failures=5, window_seconds=300.0, lockout_seconds=300.0, clock=clock
    )


def test_locks_a_key_after_the_failure_budget_is_exhausted() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(4):
        limiter.record_failure("ip:1.2.3.4")
    assert limiter.is_locked("ip:1.2.3.4") is False  # 4 < 5
    limiter.record_failure("ip:1.2.3.4")
    assert limiter.is_locked("ip:1.2.3.4") is True  # 5th trips it


def test_failures_accumulate_across_sessions_for_the_same_key() -> None:
    # The limiter is keyed, not session-scoped: repeated failures for one key from "different
    # sessions" (just repeated calls) still lock — this is the process-level guarantee.
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(5):
        limiter.record_failure("user:operator")
    assert limiter.is_locked("user:operator") is True


def test_a_different_key_is_unaffected() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(5):
        limiter.record_failure("ip:1.2.3.4")
    assert limiter.is_locked("ip:9.9.9.9") is False
    assert limiter.any_locked(["ip:9.9.9.9", "ip:1.2.3.4"]) is True


def test_lockout_expires_after_the_lockout_window() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(5):
        limiter.record_failure("ip:1.2.3.4")
    assert limiter.is_locked("ip:1.2.3.4") is True
    clock.advance(300.1)
    assert limiter.is_locked("ip:1.2.3.4") is False


def test_slow_failures_outside_the_window_never_lock() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(10):
        limiter.record_failure("ip:1.2.3.4")
        clock.advance(301.0)  # each failure is in its own fresh window
    assert limiter.is_locked("ip:1.2.3.4") is False


def test_a_success_clears_the_budget() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    for _ in range(4):
        limiter.record_failure("ip:1.2.3.4")
    limiter.record_success("ip:1.2.3.4")
    limiter.record_failure("ip:1.2.3.4")  # counter restarted → not locked
    assert limiter.is_locked("ip:1.2.3.4") is False


async def test_pbkdf2_limiter_bounds_concurrent_verifications() -> None:
    limiter = Pbkdf2Limiter(limit=2)
    lock = threading.Lock()
    live = 0
    peak = 0

    def _work(value: int) -> int:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return value

    results = await asyncio.gather(*(limiter.run(_work, i) for i in range(8)))
    assert sorted(results) == list(range(8))
    assert peak <= 2  # never more than the configured concurrency in flight at once


async def test_pbkdf2_limiter_fast_rejects_when_saturated() -> None:
    # limit=1 with a tiny acquire timeout: while one slow verification holds the slot, a second
    # attempt is fast-rejected instead of queuing indefinitely.
    limiter = Pbkdf2Limiter(limit=1, acquire_timeout=0.05)
    started = threading.Event()

    def _slow(_x: int) -> int:
        started.set()
        time.sleep(0.3)
        return _x

    holder = asyncio.create_task(limiter.run(_slow, 1))
    await asyncio.to_thread(started.wait, 1.0)  # ensure the slot is taken

    with pytest.raises(LoginThrottledError):
        await limiter.run(_slow, 2)

    assert await holder == 1
