"""Health endpoints: ``GET /health`` (liveness) and ``GET /health/ready`` (readiness + backlog).

They answer two different questions, and conflating them is how an instance goes on reporting
"healthy" while nothing actually works:

* **liveness** (``/health``) — is this PROCESS up? It opens no connection and touches no database,
  deliberately: a liveness probe that fails because the database is slow gets the container killed
  and restarted into exactly the same slow database. Its payload is unchanged, and the container
  HEALTHCHECK and the client library both depend on that.
* **readiness** (``/health/ready``) — should this process be handed traffic? It actually ASKS the
  database, and while it is there it reports the outbox backlog (R9), so an operator can see the
  queue without scraping Prometheus. Unauthenticated (a container healthcheck carries no
  credentials) and therefore strictly instance-wide: operational counts only — never a tenant,
  never a guest.

The backlog is REPORTED here but does not, by itself, make the instance "not ready": a deep queue is
no reason to pull a healthy process out of rotation — that would restart the very worker draining
it. Alerting on the backlog is what ``GET /metrics`` and
:attr:`~aethercal.server.observability.MetricsSnapshot.outbox_oldest_due_age_seconds` are for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.deps import get_session
from aethercal.server.observability import collect_metrics

router = APIRouter(tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class HealthStatus(BaseModel):
    """The liveness payload."""

    status: str


class OutboxBacklog(BaseModel):
    """The queue, as readiness sees it. Instance-wide counts; nothing that says whose."""

    pending: int
    claimed: int
    failed: int
    dead: int
    skipped: int
    due: int
    """Intents whose send time has PASSED and which are still undelivered — the REAL backlog.

    Not the same thing as ``pending``: the outbox doubles as the durable scheduler, so a 24 h
    reminder for a booking three weeks out is ``pending`` and in perfect health."""
    oldest_due_age_seconds: float
    """How long the oldest due intent has waited. Unbounded growth means nothing is draining."""


class ReadyStatus(BaseModel):
    """The readiness payload: can this process serve, and what is the queue doing?"""

    status: str
    database: str
    outbox: OutboxBacklog


@router.get("/health")
async def health() -> HealthStatus:
    """Report that the process is up. Deliberately unauthenticated and DB-free (liveness only)."""
    return HealthStatus(status="ok")


@router.get("/health/ready")
async def ready(session: SessionDep) -> ReadyStatus:
    """Report whether this process can serve, and how deep the outbox backlog is (R9).

    ==It FAILS (503) when the database is unreachable.== That is the entire point of a readiness
    probe: one that answers "ready" no matter what has told you nothing. ``/health`` above opens no
    connection at all, so it stays cheerfully green while the database is on fire — which is correct
    for liveness, and useless as a statement that this instance can do its job.
    """
    try:
        snapshot = await collect_metrics(session, now=datetime.now(UTC))
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

    counts = snapshot.outbox_by_status
    return ReadyStatus(
        status="ready",
        database="up",
        outbox=OutboxBacklog(
            pending=counts["pending"],
            claimed=counts["claimed"],
            failed=counts["failed"],
            dead=counts["dead"],
            skipped=counts["skipped"],
            due=snapshot.outbox_due,
            oldest_due_age_seconds=snapshot.outbox_oldest_due_age_seconds,
        ),
    )


__all__ = ["router"]
