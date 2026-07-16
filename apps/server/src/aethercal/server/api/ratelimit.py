"""A per-address rate limit for the ANONYMOUS surface. ==The API had none. Anywhere.==

The only limiter that ever existed in this product lives in the booking PAGE, on four of its POST
handlers. It never guarded the API: a caller talking to the server directly walked straight past it.
That was tolerable while every write demanded an API key. It stops being tolerable on the day one of
them does not — which is the day this cut lands.

.. rubric:: What it guards, and what it deliberately does not

It is mounted over ``/api/v1/public`` and nothing else. A tenant's own integration authenticates
with
a key: it has an identity that is not an address, a relationship with the business, and a quota
conversation that is not this one. Throttling it by IP would throttle a whole office behind one NAT
for the sins of a stranger.

.. rubric:: A second sliding window, and why it is not merged with the page's

``apps/booking`` has one too. They are two distributable packages, and the ``import-linter``
contract
forbids them to import each other (siblings in one layer); the only home they share is
``aethercal-core``, which is the pure date/domain engine and not where an HTTP flood control
belongs.
Merging them means either breaking the layering or widening core's remit. Both are worse than thirty
lines each process owns, sizes for its own traffic, and can reason about alone. The duplication is a
DECISION, written down here rather than left to be discovered.

.. rubric:: In-process, and honest about it

The state is a dict in this worker. Two replicas hold two budgets, so the effective ceiling is
``replicas x limit``. That is a documented bound on abuse, not a hard legal limit — the hard one
belongs at the CDN/reverse proxy, where a flood should die before it ever reaches Python. What this
buys is that a caller who goes AROUND the CDN still meets a ceiling instead of nothing at all.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from aethercal.server.client_ip import TrustedProxies, resolve_client_ip

DEFAULT_MAX_REQUESTS = 30
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MAX_KEYS = 10_000
"""A hard cap on the tracked keys. ==The limiter's own memory is an attack surface==: an unbounded
key set is a slow OOM, driven by exactly the traffic it exists to limit."""

PUBLIC_PREFIX = "/api/v1/public"


class SlidingWindowLimiter:
    """A sliding-window limiter keyed by client identity. In-process, bounded, injectable.

    The clock is a parameter (``now``), so the tests state time rather than sleeping through it, and
    ``max_keys`` LRU-evicts — the tracked set stays proportional to the ACTIVE clients, not to every
    client that ever knocked once.
    """

    def __init__(
        self,
        *,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        # LRU-ordered: most recently used at the end, so the stalest keys sit at the front.
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit for ``key`` and report whether it is within the window's budget."""
        current = now if now is not None else time.monotonic()
        window_start = current - self._window_seconds
        hits = self._hits.pop(key, None) or []  # pop, so the re-insert lands at the MRU end
        while hits and hits[0] < window_start:
            hits.pop(0)  # chronological by construction → a prefix-pop is correct and cheap
        allowed = len(hits) < self._max_requests
        if allowed:
            hits.append(current)
        if hits:  # an empty window leaves no key behind
            self._hits[key] = hits
        self._sweep(window_start)
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
        return allowed

    def _sweep(self, window_start: float) -> None:
        """Drop fully-expired keys from the LRU front, stopping at the first still-live one."""
        while self._hits:
            oldest = next(iter(self._hits))
            hits = self._hits[oldest]
            if hits and hits[-1] >= window_start:
                break
            del self._hits[oldest]

    def key_count(self) -> int:
        """How many client keys are tracked right now (the memory-safety seam)."""
        return len(self._hits)


class PublicRateLimitMiddleware:
    """Rate-limit ``/api/v1/public`` per client address, BEFORE the body is read.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the limiter needs the scope's client address and
    its path, and nothing else — so a blocked request is answered without its body ever being pulled
    off the wire. Rejecting a flood *after* parsing its attacker-sized body is a curious way to save
    a server.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowLimiter,
        trusted_proxies: TrustedProxies,
        prefix: str = PUBLIC_PREFIX,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._trusted = trusted_proxies
        self._prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(self._prefix):
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        # An address we cannot resolve gets ONE shared bucket, never a free pass: "unknown" must not
        # be the cheapest identity on the instance to obtain.
        key = resolve_client_ip(request, self._trusted) or "unknown"
        if not self._limiter.allow(key):
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": "Too many requests; try again shortly",
                },
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


__all__ = [
    "DEFAULT_MAX_KEYS",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_WINDOW_SECONDS",
    "PUBLIC_PREFIX",
    "PublicRateLimitMiddleware",
    "SlidingWindowLimiter",
]
