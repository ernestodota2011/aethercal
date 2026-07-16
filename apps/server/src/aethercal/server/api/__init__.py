"""API v1 router aggregator.

``api_router`` carries the ``/api/v1`` prefix and is mounted once by ``create_app``.

Extension pattern for later waves: a feature wave adds ``api/<feature>.py`` exposing a module-level
``router`` (an ``APIRouter``); the orchestrator wires
``api_router.include_router(<feature>.router)`` here at integration time. Do NOT pre-add feature
imports — Ola 0 mounts only ``health`` so the waves never collide in this file. Protected routes
declare ``Annotated[AuthContext, Depends(require_api_key)]`` from ``api/auth.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from aethercal.server.api import (
    bookings,
    event_types,
    health,
    schedules,
    slots,
    webhooks,
    workflows,
)

API_V1_PREFIX = "/api/v1"
"""The one place the version prefix is spelled.

``create_app`` mounts the PUBLIC router separately — it is conditional, because an unauthenticated
write endpoint is opt-in — and it must land under the same prefix as everything else. Including it
INTO ``api_router`` instead would be worse than untidy: that object is a module-level SINGLETON, so
a
second ``create_app`` in the same process (which is what the test suite does, many times per run)
would mount the same routes onto it again, and again.
"""

api_router = APIRouter(prefix=API_V1_PREFIX)
api_router.include_router(health.router)
api_router.include_router(event_types.router)
api_router.include_router(schedules.router)
api_router.include_router(slots.router)
api_router.include_router(bookings.router)
api_router.include_router(webhooks.router)
api_router.include_router(workflows.router)
# A template is the TENANT's, not one rule's — hence its own collection, never a nested one.
api_router.include_router(workflows.templates_router)

# ==`metrics` is NOT included here any more, and its absence is the point.==
#
# `GET /metrics` reads the outbox backlog across EVERY business. In THIS process — the app role,
# under row-level security, with no business bound — that query returns zero rows, and the collector
# fills its gauges with zeros. The endpoint whose whole reason for existing is "a dead drain must
# not fail in silence" would have become the loudest silence in the system: a perfect 200, every
# gauge at zero, a green dashboard, and nothing being delivered to anybody.
#
# It now lives in `api/operator.py`, served by the `aethercal-worker` process — which holds the
# BYPASSRLS scan pool that makes those numbers true, and which is also where the process-local
# DRAIN_COUNTERS are actually accumulated. See `aethercal.server.worker`.

__all__ = ["API_V1_PREFIX", "api_router"]
