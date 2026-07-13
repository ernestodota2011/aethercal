"""The in-process background scheduler wiring (F1-12 / RF-19).

Three layers, isolated so the wiring is fully offline-testable:

* **the seam** — :class:`SchedulerLike` is the tiny "register a job / start / stop" protocol. Tests
  inject a fake that records the registrations; production passes a live ``AsyncIOScheduler`` (held
  behind an ``Any`` seam, ``# pragma: no cover - live``).
* **the wiring** — ``register_scheduler_jobs`` / ``start_scheduler`` / ``stop_scheduler`` decide
  WHICH jobs (outbound-webhook delivery + busy-cache refresh + transactional-outbox drain) at WHICH
  intervals get registered, and drive start/stop. This is what the fake-scheduler tests assert.
* **the guarded ticks** — :func:`run_webhook_delivery_once`, :func:`run_busy_refresh_once` and
  :func:`run_outbox_drain_once` open their OWN ``AsyncSession`` per tick and swallow any failure, so
  one bad tick logs and returns rather than killing the loop. :func:`refresh_all_busy_caches`
  refreshes every active connection and skips a failing one without stopping the rest.

The live tick *closures* (:func:`make_webhook_delivery_tick` / :func:`make_busy_refresh_tick` /
:func:`make_outbox_drain_tick`) read the runtime effects the app's lifespan puts on ``app.state``
(``sessionmaker`` / ``http_client`` / ``fernet_key`` / ``email_sender``) and are
``# pragma: no cover - live`` — importing this module starts no scheduler.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import TimeInterval
from aethercal.server.db.models import ExternalConnection
from aethercal.server.services.calendars import (
    ServiceFactory,
    build_live_service,
    refresh_busy_cache,
)
from aethercal.server.services.outbox import (
    OutboxExecutor,
    OutboxReport,
    drain_outbox,
    make_booking_effect_executor,
)
from aethercal.server.webhooks.allowlist import PrivateTargetAllowlist
from aethercal.server.webhooks.delivery import DeliveryReport, deliver_due

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)

# Stable job ids so a restart replaces (never duplicates) each recurring job.
WEBHOOK_DELIVERY_JOB_ID = "webhook-delivery"
BUSY_REFRESH_JOB_ID = "busy-cache-refresh"
OUTBOX_DRAIN_JOB_ID = "outbox-drain"

# The outbound-webhook worker fires often (near-real-time delivery); the busy-cache refresh is
# heavier and only needs to keep the slot math roughly warm, so it runs every few minutes. The
# outbox drain carries the booking's post-commit effects (email/Google), so it fires often like the
# webhook worker to keep the guest's confirmation timely.
DEFAULT_WEBHOOK_INTERVAL_SECONDS = 60
DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS = 300
DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS = 60

# How far ahead the periodic busy-cache refresh pulls freebusy for each connected calendar.
BUSY_REFRESH_HORIZON = timedelta(days=30)

# Per-tick HTTP client timeout for outbound-webhook POSTs (a slow subscriber never stalls a tick).
WEBHOOK_HTTP_TIMEOUT_SECONDS = 10.0

Tick = Callable[[], Awaitable[None]]


class SchedulerLike(Protocol):
    """The scheduler seam: register an interval job, start, and stop. APScheduler is untyped, so the
    live object is passed as ``Any`` (assignable to this) while the test fake matches it exactly.
    """

    def add_job(
        self,
        func: Callable[..., Any],
        *,
        trigger: str,
        seconds: int,
        id: str,
        replace_existing: bool,
    ) -> Any: ...

    def start(self) -> Any: ...

    def shutdown(self, wait: bool = ...) -> Any: ...


# --------------------------------------------------------------------------------------
# Wiring — which jobs, which intervals, start/stop. Driven by a fake scheduler in tests.
# --------------------------------------------------------------------------------------


def register_scheduler_jobs(  # noqa: PLR0913 - one keyword per recurring job/interval (a flat seam)
    scheduler: SchedulerLike,
    *,
    webhook_tick: Tick,
    busy_refresh_tick: Tick,
    outbox_tick: Tick,
    webhook_interval_seconds: int = DEFAULT_WEBHOOK_INTERVAL_SECONDS,
    busy_refresh_interval_seconds: int = DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS,
    outbox_drain_interval_seconds: int = DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS,
) -> None:
    """Register the recurring interval jobs (webhook delivery + busy-cache refresh + outbox drain).

    ``replace_existing`` keeps a restart idempotent — a re-registered id overwrites rather than
    duplicating the job.
    """
    scheduler.add_job(
        webhook_tick,
        trigger="interval",
        seconds=webhook_interval_seconds,
        id=WEBHOOK_DELIVERY_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        busy_refresh_tick,
        trigger="interval",
        seconds=busy_refresh_interval_seconds,
        id=BUSY_REFRESH_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        outbox_tick,
        trigger="interval",
        seconds=outbox_drain_interval_seconds,
        id=OUTBOX_DRAIN_JOB_ID,
        replace_existing=True,
    )


def start_scheduler(  # noqa: PLR0913 - one keyword per recurring job/interval (a flat seam)
    scheduler: SchedulerLike,
    *,
    webhook_tick: Tick,
    busy_refresh_tick: Tick,
    outbox_tick: Tick,
    webhook_interval_seconds: int = DEFAULT_WEBHOOK_INTERVAL_SECONDS,
    busy_refresh_interval_seconds: int = DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS,
    outbox_drain_interval_seconds: int = DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS,
) -> None:
    """Register the interval jobs, then start the scheduler."""
    register_scheduler_jobs(
        scheduler,
        webhook_tick=webhook_tick,
        busy_refresh_tick=busy_refresh_tick,
        outbox_tick=outbox_tick,
        webhook_interval_seconds=webhook_interval_seconds,
        busy_refresh_interval_seconds=busy_refresh_interval_seconds,
        outbox_drain_interval_seconds=outbox_drain_interval_seconds,
    )
    scheduler.start()


def stop_scheduler(scheduler: SchedulerLike) -> None:
    """Stop the scheduler without blocking on in-flight jobs (clean shutdown on app teardown)."""
    scheduler.shutdown(wait=False)


# --------------------------------------------------------------------------------------
# Guarded ticks — each opens its own session; a failing tick logs and returns, never raises.
# --------------------------------------------------------------------------------------


async def run_webhook_delivery_once(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    fernet_key: bytes,
    allowlist: PrivateTargetAllowlist,
    now: datetime | None = None,
) -> DeliveryReport | None:
    """One outbound-webhook delivery pass, in its own transaction. Returns the report, or ``None``
    if the tick failed (logged) — a failure never propagates, so the scheduler keeps ticking.

    ``allowlist`` is a required keyword and is threaded straight through to :func:`deliver_due`. It
    is NOT read from the environment here: the process edge reads it once, at boot, so a bad CIDR
    fails startup rather than every tick — and so this function stays offline-testable.
    """
    moment = now if now is not None else datetime.now(UTC)
    try:
        async with sessionmaker() as session, session.begin():
            return await deliver_due(
                session, http_client, now=moment, fernet_key=fernet_key, allowlist=allowlist
            )
    except Exception:
        _logger.exception("webhook-delivery tick failed; scheduler continues")
        return None


async def run_outbox_drain_once(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    execute: OutboxExecutor,
    now: datetime | None = None,
) -> OutboxReport | None:
    """One transactional-outbox drain pass. Returns the report, or ``None`` if the tick failed
    (logged) — a failure never propagates, so the scheduler keeps ticking.

    The ``sessionmaker`` is handed to :func:`drain_outbox` rather than a session: the drain owns its
    own transaction BOUNDARIES (claim, commit, run the network I/O with nothing open, then settle).
    Opening one long transaction here and passing the session down would put every SMTP/Google call
    back inside it — exactly the lock-across-I/O bug the claim/lease design removes (R8).
    """
    moment = now if now is not None else datetime.now(UTC)
    try:
        return await drain_outbox(sessionmaker, now=moment, execute=execute)
    except Exception:
        _logger.exception("outbox-drain tick failed; scheduler continues")
        return None


async def refresh_all_busy_caches(
    session: AsyncSession,
    *,
    now: datetime,
    window: TimeInterval,
    service_factory: ServiceFactory,
) -> int:
    """Refresh the busy cache for every active (non-revoked) connection over ``window`` (RF-12).

    Each connection is refreshed inside its OWN ``SAVEPOINT`` (``session.begin_nested()``) so a
    failure is isolated to that connection: a raised ``service_factory`` (e.g. an unreachable
    Google account) OR a SQLAlchemy error mid-flush rolls back just that connection and is logged,
    while the shared session's transaction stays usable for the rest. Without the SAVEPOINT a single
    flush would deactivate the transaction and abort every remaining connection too, silently
    half-completing the batch (and leaving those busy caches stale, which the slots engine then has
    to treat conservatively). Returns how many refreshed successfully. The per-connection writes are
    flushed by :func:`refresh_busy_cache` into their SAVEPOINTs; the caller owns the outer commit.
    """
    connections = list(
        (
            await session.scalars(
                select(ExternalConnection).where(ExternalConnection.revoked_at.is_(None))
            )
        ).all()
    )
    refreshed = 0
    for connection in connections:
        try:
            # A SAVEPOINT per connection: on any error inside, the nested transaction rolls back to
            # the SAVEPOINT (recovering the outer transaction) and re-raises to the handler below.
            async with session.begin_nested():
                service = service_factory(connection)
                await refresh_busy_cache(
                    session, connection=connection, window=window, now=now, service=service
                )
            refreshed += 1
        except Exception:
            _logger.exception(
                "busy-cache refresh failed for connection %s (tenant %s); continuing",
                connection.id,
                connection.tenant_id,
            )
    return refreshed


async def run_busy_refresh_once(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    service_factory: ServiceFactory,
    now: datetime | None = None,
    horizon: timedelta = BUSY_REFRESH_HORIZON,
) -> int | None:
    """One busy-cache refresh pass over all active connections, in its own transaction. Returns the
    number refreshed, or ``None`` if the whole tick failed (logged) — never propagates.
    """
    moment = now if now is not None else datetime.now(UTC)
    window = TimeInterval(start=moment, end=moment + horizon)
    try:
        async with sessionmaker() as session, session.begin():
            return await refresh_all_busy_caches(
                session, now=moment, window=window, service_factory=service_factory
            )
    except Exception:
        _logger.exception("busy-refresh tick failed; scheduler continues")
        return None


# --------------------------------------------------------------------------------------
# Live wiring — reads app.state effects + builds the real AsyncIOScheduler. Never run offline.
# --------------------------------------------------------------------------------------


def make_webhook_delivery_tick(app: FastAPI) -> Tick:  # pragma: no cover - live
    """Bind a webhook-delivery tick to ``app.state`` (sessionmaker / http_client / fernet_key).

    ``webhook_allowlist`` is put on ``app.state`` by ``create_app``, eagerly — not by the lifespan —
    so it exists on every shape of the app, and the tick cannot fall back to "nothing allowed" for
    an instance whose operator DID declare their network.
    """

    async def _tick() -> None:
        await run_webhook_delivery_once(
            sessionmaker=app.state.sessionmaker,
            http_client=app.state.http_client,
            fernet_key=app.state.fernet_key,
            allowlist=app.state.webhook_allowlist,
        )

    return _tick


def make_busy_refresh_tick(app: FastAPI) -> Tick:  # pragma: no cover - live
    """Bind a busy-cache refresh tick to ``app.state`` (sessionmaker + a Fernet-built factory)."""

    async def _tick() -> None:
        fernet = Fernet(app.state.fernet_key)
        service_factory: ServiceFactory = functools.partial(build_live_service, fernet=fernet)
        await run_busy_refresh_once(
            sessionmaker=app.state.sessionmaker, service_factory=service_factory
        )

    return _tick


def make_outbox_drain_tick(app: FastAPI) -> Tick:  # pragma: no cover - live
    """Bind an outbox-drain tick to ``app.state`` (sessionmaker + SMTP sender + a Fernet-built
    Google factory) — the live ``execute`` dispatches each intent to its handler (email / Google).
    """

    async def _tick() -> None:
        fernet = Fernet(app.state.fernet_key)
        service_factory: ServiceFactory = functools.partial(build_live_service, fernet=fernet)
        execute = make_booking_effect_executor(
            sessionmaker=app.state.sessionmaker,
            sender=app.state.email_sender,
            service_factory=service_factory,
            # The non-email channel registry. EMPTY today, and that is not a gap: a step on a
            # channel nobody registered is SKIPPED with its reason, never failed — a channel
            # without credentials is a disabled feature, not an error. The channels cut registers
            # its WhatsApp/SMS senders here and touches nothing else.
            #
            # Email is deliberately NOT here: it goes through the full composer, which carries the
            # .ics invite that a plain-body ChannelSender cannot.
            channels=getattr(app.state, "channel_senders", None),
        )
        await run_outbox_drain_once(sessionmaker=app.state.sessionmaker, execute=execute)

    return _tick


def build_interval_scheduler() -> Any:  # pragma: no cover - live
    """Construct the live in-memory ``AsyncIOScheduler`` for the recurring interval jobs.

    Recurring maintenance jobs live in the default in-memory jobstore (their tick closures are not
    picklable and never need to survive a restart — the loop self-heals on the next interval).

    This is now the ONLY scheduler. Per-booking reminders used to run on a second, PERSISTENT one
    (an APScheduler ``SQLAlchemyJobStore``); durability for them now comes from the transactional
    outbox, whose ``next_retry_at`` is the send time and whose ``dedupe_key`` is the single
    idempotency barrier. Two schedulers meant two barriers that did not know about each other — and
    a guest getting two reminders the moment a tenant defined a "24 h before" workflow rule.
    """
    return AsyncIOScheduler()


__all__ = [
    "BUSY_REFRESH_HORIZON",
    "BUSY_REFRESH_JOB_ID",
    "DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS",
    "DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS",
    "DEFAULT_WEBHOOK_INTERVAL_SECONDS",
    "OUTBOX_DRAIN_JOB_ID",
    "WEBHOOK_DELIVERY_JOB_ID",
    "WEBHOOK_HTTP_TIMEOUT_SECONDS",
    "SchedulerLike",
    "Tick",
    "build_interval_scheduler",
    "make_busy_refresh_tick",
    "make_outbox_drain_tick",
    "make_webhook_delivery_tick",
    "refresh_all_busy_caches",
    "register_scheduler_jobs",
    "run_busy_refresh_once",
    "run_outbox_drain_once",
    "run_webhook_delivery_once",
    "start_scheduler",
    "stop_scheduler",
]
