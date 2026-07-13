"""The OPERATOR surface — served by the ``aethercal-worker`` process, and only by it.

``GET /metrics`` (Prometheus, operator token) · ``GET /health`` (liveness) · ``GET /health/ready``
(readiness **with the outbox backlog**).

.. rubric:: Why this moved out of the web process

This is what makes a dead drain visible: if the drain dies, the emails stop going out **in silence**
and nothing reports it. Under row-level security, served from the WEB process, it would have become
the very thing it was built to prevent:

* every query in ``collect_metrics`` is cross-business — there is not one ``WHERE tenant_id`` in it,
  and there is not supposed to be: this is the operator's view of the whole instance;
* the web process holds ``aethercal_app``, under RLS, and a scrape carries no business to bind;
* so every one of those queries would have returned **zero rows** — and the collector fills zeros in
  wherever it finds nothing.

``200 OK``. ``aethercal_outbox_due 0``. ``aethercal_outbox_oldest_due_age_seconds 0``. For ever,
with
the queue on fire. ==RLS would have turned the dead-man switch into the corpse.==

So the endpoint lives here, in the worker: the process that HOLDS the ``BYPASSRLS`` scan pool (so
the
numbers are true), and the process that actually drains (so ``DRAIN_COUNTERS`` — which are
process-local — are at last accumulated where they are published). ``aethercal_scheduler_enabled``
went with the move: it existed only to warn an operator that they were scraping a process which does
not drain, and that process no longer serves metrics at all.

.. rubric:: Why it has its own token, and why "unconfigured" means CLOSED

The endpoint reports the WHOLE instance: the outbox backlog, the booking counts, the no-show rate.
That is the **operator's** view, and on a multi-business instance it is every tenant's volume laid
out side by side. So:

* a tenant's API key does not open it — a perfectly valid key belonging to one business is not
  authority over the numbers of all of them;
* it is guarded by ``AETHERCAL_METRICS_TOKEN``, an operator secret, compared in constant time;
* and with **no token configured the endpoint is CLOSED (503), not open.** This is a public
  repository and its instances are exposed. "Nobody set the variable, so we served the pipeline of
  every business on the box to whoever asked" is the silent no-op with the worst blast radius
  available, so the default is refusal — loudly, naming the variable in the answer.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from aethercal.server.api.auth import bearer_token
from aethercal.server.db.pools import BypassReason, WorkerPools
from aethercal.server.observability import (
    CONTENT_TYPE,
    DRAIN_COUNTERS,
    MetricsSnapshot,
    collect_metrics,
    render_prometheus,
)
from aethercal.server.settings import Settings

router = APIRouter(tags=["operator"])


class HealthStatus(BaseModel):
    """The worker's liveness payload — the same shape as the web's, deliberately."""

    status: str


class OutboxBacklog(BaseModel):
    """The queue, as the worker's readiness sees it. Instance-wide counts; nothing that says
    whose."""

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
    """The worker's readiness: can it reach the database, and what is the queue doing?"""

    status: str
    database: str
    outbox: OutboxBacklog


def _pools(request: Request) -> WorkerPools:
    pools: WorkerPools = request.app.state.pools
    return pools


def _token_matches(presented: str, configured: str) -> bool:
    """Constant-time compare, ==over BYTES, so that it CANNOT raise.==

    ``secrets.compare_digest`` accepts ``str`` only when BOTH sides are ASCII, and raises
    ``TypeError`` otherwise. The presented token is attacker-controlled: one accented character in
    the header crashed this comparison, and the endpoint whose entire purpose is to TELL YOU
    SOMETHING IS WRONG answered 500 — from inside its own auth check, to an unauthenticated caller.
    ==A 500 on the observability surface is the worst 500 available:== the operator debugging an
    outage finds their instruments broken by the very request they made to read them.

    Encoding both sides explicitly makes the comparison TOTAL: every possible header is now either a
    match or a 401, and nothing is left that can throw. (The configured side is already guaranteed
    ASCII — ``Settings`` refuses to boot otherwise — so this is the belt to that braces, and it
    guards the one input we do not control.)
    """
    return secrets.compare_digest(presented.encode("utf-8"), configured.encode("utf-8"))


def _require_operator(request: Request) -> None:
    """Authorise a scrape with the OPERATOR token, or refuse. ==Never with a tenant's API key.==

    503 when no token is configured (the endpoint is OFF, and OFF is closed); 401 when the token is
    missing, wrong, or unpresentable. Constant-time, because a plain ``==`` over a secret leaks its
    prefix to a patient attacker one byte at a time.
    """
    settings: Settings = request.app.state.settings
    configured = settings.metrics_token
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "metrics_disabled",
                "message": (
                    "The metrics endpoint is not configured. Set AETHERCAL_METRICS_TOKEN to a long "
                    "random value and scrape it with 'Authorization: Bearer <token>'. It is closed "
                    "rather than open by default: it reports instance-wide data."
                ),
            },
        )
    presented = bearer_token(request)
    if presented is None or not _token_matches(presented, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Invalid or missing metrics token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _snapshot(pools: WorkerPools) -> MetricsSnapshot:
    """One instance-wide read, on the ONE pool where those numbers can be true.

    ``scan_session(OPERATOR_METRICS)`` is not decoration: ``collect_metrics`` REFUSES a session with
    no bypass marker. There is therefore no way to reach it from a pool under RLS — and so no way to
    publish an instance-wide gauge that is silently reporting on nothing at all.
    """
    async with pools.scan_session(BypassReason.OPERATOR_METRICS) as session:
        return await collect_metrics(session, now=datetime.now(UTC))


@router.get("/health")
async def health() -> HealthStatus:
    """The worker is up. DB-free, like the web's — a slow database must not get the drain killed."""
    return HealthStatus(status="ok")


@router.get("/health/ready")
async def ready(request: Request) -> ReadyStatus:
    """The worker's readiness — ==and the ONE place the backlog is reported truthfully.==

    The web's ``/health/ready`` deliberately no longer carries this block: unauthenticated, on the
    app role, with no business bound, it could only ever have reported zeros — a permanently ready
    probe over a permanently burning queue. Here there is a bypass pool, so the numbers mean what
    they say.

    A deep queue does NOT, by itself, make the worker "not ready": pulling a healthy drain out of
    rotation would restart the very process draining it. The backlog is REPORTED so an operator can
    see it without scraping Prometheus; alerting on it is what ``GET /metrics`` and
    ``aethercal_outbox_oldest_due_age_seconds`` are for.
    """
    pools = _pools(request)
    try:
        snapshot = await _snapshot(pools)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            # No exception text in the body: the connection string — credentials and all — is in
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


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(request: Request) -> PlainTextResponse:
    """The Prometheus text exposition for this instance (operator token required).

    ==Instance-wide only.== No tenant id, no slug, no guest, no event title — not in a label, not in
    a value. :func:`~aethercal.server.observability.render_prometheus` owns that guarantee, and a
    test asserts it against the real values rather than against the word "tenant".
    """
    _require_operator(request)
    snapshot = await _snapshot(_pools(request))
    body = render_prometheus(snapshot, counters=DRAIN_COUNTERS)
    return PlainTextResponse(content=body, media_type=CONTENT_TYPE)


__all__ = ["router"]
