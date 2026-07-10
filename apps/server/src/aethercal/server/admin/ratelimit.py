"""Process-level login rate limiting + PBKDF2 concurrency bounding (F1-11, RF-18).

This is a **defense-in-depth** layer, not the primary defense. The primary brute-force defense for a
mounted admin is at the reverse proxy (CF WAF / fail2ban rate-limiting on ``/admin``), reinforced by
the deliberately slow, constant-time PBKDF2. What this module adds:

* :class:`LoginRateLimiter` — a **process-global**, per-key failed-login counter with a sliding
  window and a lockout. It is keyed by an opaque string; the login handler keys by client IP *and*
  by username, so failures accumulate across DIFFERENT websocket sessions from the same source — a
  new session does NOT reset the budget (the hole the earlier per-session lockout left open). It is
  in-memory and single-process: correct for the single-process MVP; a multi-replica deploy needs a
  shared store (e.g. Redis) — and, above all, the proxy-level limit.
* :class:`Pbkdf2Limiter` — a bounded-concurrency runner for the (CPU-heavy) PBKDF2 verification, so
  a flood of simultaneous login attempts cannot exhaust CPU/threads. The verification also runs off
  the event loop (``asyncio.to_thread``) so it never blocks request handling.

Both are exposed as process-global singletons (:data:`LOGIN_LIMITER`, :data:`PBKDF2_LIMITER`) the
state handler uses; tests construct their own instances with an injected clock.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

_T = TypeVar("_T")

# Defaults: five failures per key within five minutes → a five-minute lockout for that key.
_DEFAULT_MAX_FAILURES = 5
_DEFAULT_WINDOW_SECONDS = 300.0
_DEFAULT_LOCKOUT_SECONDS = 300.0
# Cap simultaneous PBKDF2 derivations so a login flood is not a CPU DoS, and fast-reject once a
# caller has waited longer than this for a slot (bounds the pending queue instead of letting it grow
# without limit under saturation).
_DEFAULT_PBKDF2_CONCURRENCY = 4
_DEFAULT_ACQUIRE_TIMEOUT = 10.0


class LoginThrottledError(Exception):
    """The PBKDF2 verifier is saturated; the attempt is fast-rejected rather than queued forever."""


@dataclass
class _Bucket:
    """The failure state for one key: a count within the current window, plus a lockout deadline."""

    window_start: float
    failures: int = 0
    locked_until: float = 0.0


class LoginRateLimiter:
    """A process-global, per-key failed-login limiter (see the module docstring)."""

    def __init__(
        self,
        *,
        max_failures: int = _DEFAULT_MAX_FAILURES,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        lockout_seconds: float = _DEFAULT_LOCKOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def is_locked(self, key: str) -> bool:
        """Whether ``key`` is currently locked out."""
        bucket = self._buckets.get(key)
        return bucket is not None and self._clock() < bucket.locked_until

    def any_locked(self, keys: Iterable[str]) -> bool:
        """Whether ANY of ``keys`` is currently locked out (IP or username)."""
        return any(self.is_locked(key) for key in keys)

    def record_failure(self, key: str) -> None:
        """Record a failed attempt for ``key``; lock it out once it exceeds the window budget."""
        now = self._clock()
        self._prune(now)
        bucket = self._buckets.get(key)
        if bucket is None or now - bucket.window_start > self._window:
            bucket = _Bucket(window_start=now)
            self._buckets[key] = bucket
        bucket.failures += 1
        if bucket.failures >= self._max:
            bucket.locked_until = now + self._lockout
            bucket.failures = 0
            bucket.window_start = now

    def record_success(self, key: str) -> None:
        """Clear any failure state for ``key`` (a good login resets its budget)."""
        self._buckets.pop(key, None)

    def reset(self) -> None:
        """Drop all state (used by tests; a fresh process starts empty anyway)."""
        self._buckets.clear()

    def _prune(self, now: float) -> None:
        """Drop buckets that are neither locked nor within their window (bounds memory growth)."""
        dead = [
            key
            for key, bucket in self._buckets.items()
            if now >= bucket.locked_until and now - bucket.window_start > self._window
        ]
        for key in dead:
            del self._buckets[key]


class Pbkdf2Limiter:
    """Runs a (blocking) verification off-thread under a bounded, per-loop concurrency semaphore.

    The semaphore is created lazily per running event loop so it is safe both in the long-lived
    server loop and under Python 3.11, where asyncio primitives bind to the loop they are first used
    on (tests run each case in its own loop).
    """

    def __init__(
        self,
        limit: int = _DEFAULT_PBKDF2_CONCURRENCY,
        *,
        acquire_timeout: float = _DEFAULT_ACQUIRE_TIMEOUT,
    ) -> None:
        self.limit = limit
        self.acquire_timeout = acquire_timeout
        self._semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.limit)
            self._semaphores[loop] = semaphore
        return semaphore

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        """Hold one concurrency slot for the duration of the block; fast-reject when saturated.

        If no slot frees up within ``acquire_timeout`` the caller is rejected with
        :class:`LoginThrottledError` instead of queuing indefinitely. Exposing the slot (rather than
        only :meth:`run`) lets the login handler re-check the lockout *inside* the acquired turn —
        just before spending a derivation — so once concurrent failures trip the lock, queued
        attempts abort without running PBKDF2 (bounding the overshoot to the concurrency limit).
        """
        semaphore = self._semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.acquire_timeout)
        except TimeoutError as exc:
            raise LoginThrottledError from exc
        try:
            yield
        finally:
            semaphore.release()

    async def run(self, fn: Callable[..., _T], *args: object) -> _T:
        """Run ``fn(*args)`` in a worker thread, bounded to ``limit`` concurrent executions."""
        async with self.slot():
            return await asyncio.to_thread(fn, *args)


# Process-global singletons the login handler uses.
LOGIN_LIMITER = LoginRateLimiter()
PBKDF2_LIMITER = Pbkdf2Limiter()

__all__ = ["LOGIN_LIMITER", "PBKDF2_LIMITER", "LoginRateLimiter", "Pbkdf2Limiter"]
