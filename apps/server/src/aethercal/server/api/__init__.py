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

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(event_types.router)
api_router.include_router(schedules.router)
api_router.include_router(slots.router)
api_router.include_router(bookings.router)
api_router.include_router(webhooks.router)
api_router.include_router(workflows.router)
# A template is the TENANT's, not one rule's — hence its own collection, never a nested one.
api_router.include_router(workflows.templates_router)

__all__ = ["api_router"]
