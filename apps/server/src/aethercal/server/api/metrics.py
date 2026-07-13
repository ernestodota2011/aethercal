"""``GET /metrics`` — the authenticated Prometheus endpoint (R9).

This is what makes a dead scheduler visible: today, if the drain process dies, the emails stop going
out **in silence** and nothing reports it. What the numbers mean — and which of them is the dead-man
switch — is documented in :mod:`aethercal.server.observability`.

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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from aethercal.server.api.auth import bearer_token
from aethercal.server.deps import get_session
from aethercal.server.observability import (
    CONTENT_TYPE,
    DRAIN_COUNTERS,
    collect_metrics,
    render_prometheus,
)
from aethercal.server.settings import Settings

router = APIRouter(tags=["metrics"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(request: Request, session: SessionDep) -> PlainTextResponse:
    """The Prometheus text exposition for this instance (operator token required).

    ==Instance-wide only.== No tenant id, no slug, no guest, no event title — not in a label, not in
    a value. :func:`~aethercal.server.observability.render_prometheus` owns that guarantee, and a
    test asserts it against the real values rather than against the word "tenant".
    """
    _require_operator(request)
    settings: Settings = request.app.state.settings
    snapshot = await collect_metrics(session, now=datetime.now(UTC))
    body = render_prometheus(
        snapshot,
        counters=DRAIN_COUNTERS,
        # The drain counters are process-local, and the container runs the scheduler in exactly ONE
        # process (deploy/README.md). Publishing whether THIS process is that one is what stops an
        # operator reading a zero on an API-only worker as "nothing was ever lost".
        scheduler_enabled=settings.run_scheduler,
    )
    return PlainTextResponse(content=body, media_type=CONTENT_TYPE)


__all__ = ["router"]
