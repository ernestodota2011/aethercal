"""Health endpoints of the WEB process: ``GET /health`` (liveness) and ``/health/ready``
(readiness).

They answer two different questions, and conflating them is how an instance goes on reporting
"healthy" while nothing actually works:

* **liveness** (``/health``) — is this PROCESS up? It opens no connection and touches no database,
  deliberately: a liveness probe that fails because the database is slow gets the container killed
  and restarted into exactly the same slow database. Its payload is unchanged, and the container
  HEALTHCHECK and the client library both depend on that.
* **readiness** (``/health/ready``) — should this process be handed traffic? It actually ASKS the
  database. Unauthenticated (a container healthcheck carries no credentials) and therefore strictly
  instance-wide: it reports nothing but its own reachability — never a tenant, never a guest.

.. rubric:: ==Why the outbox backlog is NO LONGER served here — the second dead-man switch==

This endpoint used to report the outbox backlog, by calling the same cross-business
``collect_metrics`` that ``GET /metrics`` calls. Under row-level security that would have been a
catastrophe of precisely the kind this product keeps writing about:

* it is **unauthenticated**, so it can hold no tenant's authority and bind no business;
* it runs in the **web** process, on the app role, where a cross-business query returns **zero
  rows**;
* and ``collect_metrics`` **fills with zeros by construction** — it never raises.

The result would have been ``status: "ready"``, ``database: "up"``, ``outbox.due = 0``,
``oldest_due_age_seconds = 0.0`` — **for ever, with the outbox on fire** — on the very probe the
deployment uses to decide whether this instance is healthy. The first dead-man switch (``/metrics``)
was rescued by moving it to the worker; this one would have been left behind, lying in green.

So the backlog moves to where it means something and where the bypass exists: the **worker**'s own
``/health/ready`` (:mod:`aethercal.server.api.operator`). What stays here is the half that is both
honest and useful in this process — *can I reach the database at all* — asked with a ``SELECT 1``,
which under RLS still works and, crucially, still **fails loudly** when the database is gone.

And the root cause is closed at the root: ``collect_metrics`` now REFUSES a session without the
bypass marker. An endpoint that reinvents this mistake will not return zeros; it will crash.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.deps import get_session

router = APIRouter(tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_PING = text("SELECT 1")


class HealthStatus(BaseModel):
    """The liveness payload."""

    status: str


class ReadyStatus(BaseModel):
    """The readiness payload of the WEB process: can it reach its database?

    There is no ``outbox`` block any more — see the module docstring. The queue is the worker's to
    report, on the worker's own ``/health/ready``, where a bypass pool exists to measure it
    truthfully instead of filling it with zeros.
    """

    status: str
    database: str


@router.get("/health")
async def health() -> HealthStatus:
    """Report that the process is up. Deliberately unauthenticated and DB-free (liveness only)."""
    return HealthStatus(status="ok")


@router.get("/health/ready")
async def ready(session: SessionDep) -> ReadyStatus:
    """Can this process reach its database? ==It FAILS (503) when it cannot.==

    That is the entire point of a readiness probe: one that answers "ready" no matter what has told
    you nothing. ``/health`` above opens no connection at all, so it stays cheerfully green while
    the database is on fire — which is correct for liveness, and useless as a statement that this
    instance can do its job.

    ``SELECT 1`` is deliberately the whole of it. It is the one question this process can answer
    truthfully about the database: it needs no business bound, it is unaffected by row-level
    security, and when the database is unreachable it **raises** — which is exactly the property the
    backlog block did not have.
    """
    try:
        await session.execute(_PING)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            # No exception text in the body: the connection string — credentials and all — lives in
            # there, and this endpoint is unauthenticated. It is logged, never served.
            detail={
                "status": "degraded",
                "database": "down",
                "message": "The database is unreachable",
            },
        ) from exc

    return ReadyStatus(status="ready", database="up")


__all__ = ["router"]
