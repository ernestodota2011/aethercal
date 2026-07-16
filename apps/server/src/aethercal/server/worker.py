"""``aethercal-worker`` — the process that drains, delivers, refreshes, and says it is alive.

.. rubric:: Why this is a PROCESS and not a flag

``AETHERCAL_RUN_SCHEDULER=1`` used to start the ticks inside the web process. It never separated
anything:

* the process it produced was still a **full FastAPI app with the admin mounted** — the flag chose
  whether a scheduler *also* ran, not what the process *was*;
* and every tick was bound to ``app.state.sessionmaker``: the **request path's** pool.

The second point is what kills it under row-level security. A background tick has no request, so it
has no ``ContextVar``, so its GUC is empty, so every query it makes matches **zero rows**.
``select_due`` → 0. ``deliver_due`` → 0. ``refresh_all_busy_caches`` → 0. Not one exception. Every
confirmation, reminder, webhook and (from the payments batch) refund simply stops — and
``/metrics``,
the thing built to shout about exactly this, no longer lives in that process to do it.

No flag can fix that, because the problem is not *whether* the ticks run: it is *what they run on*.
So the ticks moved to a process that owns the right connections, and the flag became a boot failure
that names this module.

.. rubric:: The two pools

* ``worker_scan`` — ``aethercal_worker``, ``BYPASSRLS``. For the queries whose ``tenant_id`` is not
  yet known: what is due, which leases expired, which row can I claim, what are the instance-wide
  gauges. Reachable ONLY through ``WorkerPools.scan_session(BypassReason.…)``, so a new consumer
  cannot take the bypass without declaring why: the enum is exhaustive, and a structural test breaks
  CI otherwise.
* ``worker_exec`` — ``aethercal_app``, **RLS applies**. Once an item's business is known (it is on
  the row that was just claimed), everything real happens here: read the booking, decrypt that
  business's credential, run the effect, settle the row — inside a ``tenant_scope`` for that item
  and
  no other.

This is the process where a cross-business leak would be *externally visible*: business A's guest,
by
name and email, arriving at business B's webhook. Which is exactly why it is the process that got
two
pools instead of one big one.

.. rubric:: It also serves the operator surface

``/metrics`` and ``/health/ready`` (with the outbox backlog) live here now — see
:mod:`aethercal.server.api.operator`. The gauges are cross-business, so they can only be true on the
bypass pool; and the drain counters are process-local, so they can only be true in the process that
drains. Both were previously served from the web, where both were quietly false.

.. rubric:: EXACTLY ONE of these runs

The "no claim" design of ``deliver_due`` and ``refresh_all_busy_caches`` rests on that, and
``deploy/docker-compose.yml`` pins ``replicas: 1``. See ``deploy/README.md``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, TypedDict

import httpx
import uvicorn
from fastapi import APIRouter, FastAPI

from aethercal.server.api import operator
from aethercal.server.app import build_instance_sender_defaults
from aethercal.server.db.config import DATABASE_URL_ENV, WORKER_DATABASE_URL_ENV
from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.migrate import assert_schema_at_head
from aethercal.server.db.pools import WorkerPools
from aethercal.server.db.roles import DbRole, assert_engine_role
from aethercal.server.scheduler import (
    WEBHOOK_HTTP_TIMEOUT_SECONDS,
    build_interval_scheduler,
    make_busy_refresh_tick,
    make_outbox_drain_tick,
    make_webhook_delivery_tick,
    start_scheduler,
    stop_scheduler,
)
from aethercal.server.services.tenant_senders import (
    SenderClients,
    warn_if_operator_identity_is_lent,
)
from aethercal.server.settings import Settings
from aethercal.server.webhooks.allowlist import warn_if_loopback_is_allowlisted

operator_router = APIRouter(prefix="/api/v1")
operator_router.include_router(operator.router)


class SchedulerIntervals(TypedDict):
    """The tick intervals :func:`start_scheduler` takes, as keyword arguments."""

    webhook_interval_seconds: int
    busy_refresh_interval_seconds: int
    outbox_drain_interval_seconds: int


def scheduler_intervals(settings: Settings) -> SchedulerIntervals:
    """The scheduler's tick intervals, sourced from the environment (RF-19).

    A ``TypedDict`` unpacked into :func:`start_scheduler`, rather than three loose arguments, so the
    keys are type-checked against that function's keywords: a knob the scheduler does not accept
    fails to type-check, instead of being read from the environment, documented, and then silently
    dropped while the scheduler keeps ticking at its default.
    """
    return SchedulerIntervals(
        webhook_interval_seconds=settings.webhook_interval_seconds,
        busy_refresh_interval_seconds=settings.busy_refresh_interval_seconds,
        outbox_drain_interval_seconds=settings.outbox_drain_interval_seconds,
    )


def create_worker_app(settings: Settings) -> FastAPI:
    """Build the worker: two pools, three ticks, and the operator surface. No API, no admin.

    The engines and the pools are built EAGERLY (like ``create_app``'s) so a test can inspect them —
    and, more to the point, so the boot assertion in the lifespan has something to interrogate.
    """
    # AETHERCAL_WORKER_DATABASE_URL is FAIL-CLOSED: absent, this raises and the worker does not
    # start. It must never fall back to the app URL — on the app role `select_due` returns zero
    # rows,
    # so the fallback would produce a worker that runs for ever, delivers nothing, and logs nothing.
    scan_engine = build_async_engine(settings.worker_database_config())
    exec_engine = build_async_engine(settings.database_config())
    pools = WorkerPools(
        _scan_maker=build_sessionmaker(scan_engine),
        exec_maker=build_sessionmaker(exec_engine),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(scan_engine.dispose)
            stack.push_async_callback(exec_engine.dispose)

            # ==THE BOOT ASSERTION, over BOTH engines.== Swap these two URLs by accident and nothing
            # complains: the "scan" pool on the app role finds no work (zero rows, in silence), and
            # the "exec" pool on the worker role executes every effect with BYPASSRLS — which is the
            # cross-business leak this whole batch exists to make impossible. Neither of those
            # produces an exception. Asking each connection who it is, is the only detector there
            # is.
            await assert_engine_role(scan_engine, DbRole.WORKER, url_env=WORKER_DATABASE_URL_ENV)
            await assert_engine_role(exec_engine, DbRole.APP, url_env=DATABASE_URL_ENV)
            await assert_schema_at_head(exec_engine)

            http_client = httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS)
            stack.push_async_callback(http_client.aclose)
            app.state.http_client = http_client

            # ==TWO clients, and which one a sender gets is a POLICY (B-03bis).==
            #
            # `sender_clients.tenant` re-pins every request at connect through
            # `EgressGuardedTransport`, so a business's endpoint cannot be rebound into this
            # instance's network between the egress guard and the socket. `.operator` is plain: the
            # operator's own configuration, dialed as it always was. `_assert_target_reachable`
            # pairs each resolved credential with the right one, and that pairing IS the witness.
            sender_clients = SenderClients.build(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS)
            stack.push_async_callback(sender_clients.aclose)
            app.state.sender_clients = sender_clients
            app.state.fernet_key = settings.fernet_key()
            # The READ reader: the current key, plus the retiring one while a rotation is in flight.
            # The ticks decrypt with this so a row the rotation has not reached yet stays readable —
            # and they still WRITE (re-encrypt nothing here) under the current key alone.
            #
            # ==ALL THREE ticks die without this name, and none of them says so.== The webhook
            # delivery tick signs with it, `_decryption_fernet` builds the busy-refresh and drain
            # readers from it, and `resolve_senders_for` decrypts each business's credential.
            # Every tick body catches and logs, so a missing name here is an inert worker that
            # reports healthy for ever. Pinned by `tests/test_worker_sender_wiring.py`, which boots
            # this lifespan for real rather than mirroring it in a fake.
            app.state.fernet_keys = settings.decryption_fernet_keys()

            # ==The OPERATOR's defaults — not senders (B-03bis).==
            #
            # This used to build one SMTP client and one WhatsApp/SMS registry here, at boot, and
            # hand them to the drain. The drain is a loop over a batch spanning SEVERAL businesses,
            # so every one of them sent through that single object: a business's WhatsApp reminder
            # left the instance from the OPERATOR's number, and its guest replied to a stranger.
            #
            # Now the process edge reads only inert configuration, and each item's senders are
            # resolved from its own `tenant_id` inside the drain's per-item `tenant_scope`
            # (`scheduler._resolve_senders_for` → `services/tenant_senders.resolve_tenant_senders`).
            #
            # A half-configured phone channel still RAISES here and fails the boot on purpose:
            # "sending, but uncapped" must never be a state the process that actually sends can
            # reach.
            app.state.instance_sender_defaults = build_instance_sender_defaults()
            warn_if_operator_identity_is_lent(app.state.instance_sender_defaults)

            interval_scheduler: Any = build_interval_scheduler()
            start_scheduler(
                interval_scheduler,
                webhook_tick=make_webhook_delivery_tick(app),
                busy_refresh_tick=make_busy_refresh_tick(app),
                outbox_tick=make_outbox_drain_tick(app),
                **scheduler_intervals(settings),
            )
            stack.callback(stop_scheduler, interval_scheduler)
            app.state.scheduler = interval_scheduler

            yield

    app = FastAPI(title=f"{settings.app_name} worker", lifespan=lifespan)
    app.state.settings = settings
    app.state.pools = pools
    app.state.webhook_allowlist = settings.private_target_allowlist()
    warn_if_loopback_is_allowlisted(app.state.webhook_allowlist)

    app.include_router(operator_router)
    return app


def create_worker_app_from_env() -> FastAPI:
    """The uvicorn ``--factory`` entrypoint for the worker (RF-19: settings from the
    environment)."""
    return create_worker_app(Settings())  # type: ignore[call-arg]  # every field is env-sourced


def run_worker() -> None:  # pragma: no cover - live process entrypoint
    """Console-script entrypoint (``aethercal-worker``).

    Bound to ``127.0.0.1`` by default, and the asymmetry with the web process is deliberate: the
    only
    thing this one serves is the OPERATOR surface. A scrape comes from inside the deployment; a
    guest
    has no business ever reaching it.
    """
    uvicorn.run(
        "aethercal.server.worker:create_worker_app_from_env",
        factory=True,
        host=os.environ.get("AETHERCAL_WORKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("AETHERCAL_WORKER_PORT", "8001")),
    )


__all__ = [
    "SchedulerIntervals",
    "create_worker_app",
    "create_worker_app_from_env",
    "run_worker",
    "scheduler_intervals",
]
