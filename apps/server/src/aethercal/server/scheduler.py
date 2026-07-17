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

Importing this module starts no scheduler.

==What a tick DECIDES is not behind a pragma== (:func:`build_webhook_deliverer` /
:func:`build_busy_refresher` / :func:`build_drain_executor` / :func:`resolve_senders_for`). The seam
between what the boot writes onto ``app.state`` and what a tick reads back off it is where a silent
break hides, and a pragma over it buys the green without buying the truth. All three ticks read
``app.state.fernet_keys``, and an edit really did eat that line once: the worker boots clean,
reports healthy for ever, and delivers nothing. So each tick's reads live in a ``build_*`` function
driven against the worker's REAL lifespan (``tests/test_worker_tick_wiring.py``,
``tests/test_worker_sender_wiring.py``).

``make_webhook_delivery_tick`` / ``make_busy_refresh_tick`` and their closures are driven by those
tests too, and carry no pragma: a one-line closure that could call the wrong builder is not
"unreachable offline", it is unlooked-at. ==:func:`make_outbox_drain_tick` is the one that still
has one==, and it is not thin — it also builds the parked-payment tick from ``app.state.settings``.
That read is untested; the residual is stated in its own docstring rather than left to be found.
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import select

from aethercal.core.model import TimeInterval
from aethercal.server.db.guc import tenant_scope
from aethercal.server.db.models import Booking, ExternalConnection
from aethercal.server.db.pools import BypassReason, WorkerPools
from aethercal.server.services.bookings import BookingEffects, confirm_paid_booking_effects
from aethercal.server.services.calendars import (
    ServiceFactory,
    build_live_service,
    refresh_busy_cache,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import (
    OutboxExecutor,
    OutboxReport,
    drain_outbox,
    make_booking_effect_executor,
)
from aethercal.server.services.payments import build_money_runners, run_parked_payment_tick
from aethercal.server.services.tenant_senders import TenantSenders, resolve_tenant_senders
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

WebhookDeliverer = Callable[[], Awaitable[DeliveryReport | None]]
"""One fully-bound webhook-delivery pass.

==Everything :func:`make_webhook_delivery_tick` decides.=="""

BusyRefresher = Callable[[], Awaitable[int | None]]
"""One fully-bound busy-cache refresh pass.

==Everything :func:`make_busy_refresh_tick` decides.=="""

ParkedPaymentRunner = Callable[[], Awaitable[None]]
"""One fully-bound parked-payment pass — the boot-seam reads of :func:`make_outbox_drain_tick`
lifted out where a test can drive them (B-09)."""


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
    pools: WorkerPools,
    http_client: httpx.AsyncClient,
    fernet_key: bytes | Sequence[bytes],
    allowlist: PrivateTargetAllowlist,
    now: datetime | None = None,
) -> DeliveryReport | None:
    """One outbound-webhook delivery pass. Returns the report, or ``None`` if the tick failed
    (logged) — a failure never propagates, so the scheduler keeps ticking.

    ``allowlist`` is a required keyword and is threaded straight through to :func:`deliver_due`. It
    is NOT read from the environment here: the process edge reads it once, at boot, so a bad CIDR
    fails startup rather than every tick — and so this function stays offline-testable.
    """
    moment = now if now is not None else datetime.now(UTC)
    try:
        return await deliver_due(
            pools, http_client, now=moment, fernet_key=fernet_key, allowlist=allowlist
        )
    except Exception:
        _logger.exception("webhook-delivery tick failed; scheduler continues")
        return None


async def run_outbox_drain_once(
    *,
    pools: WorkerPools,
    execute: OutboxExecutor,
    now: datetime | None = None,
) -> OutboxReport | None:
    """One transactional-outbox drain pass. Returns the report, or ``None`` if the tick failed
    (logged) — a failure never propagates, so the scheduler keeps ticking.

    The ``pools`` go to :func:`drain_outbox` rather than a session: the drain owns its own
    transaction BOUNDARIES (claim, commit, run the network I/O with nothing open, then settle) — and
    it owns WHICH POOL each of them runs on: a bypass one to find work whose business it cannot know
    yet, the app one under RLS to execute it. Opening one long transaction here and passing the
    session down would put every SMTP/Google call back inside it (the lock-across-I/O bug R8
    removed)
    and would collapse the two pools back into one.
    """
    moment = now if now is not None else datetime.now(UTC)
    try:
        return await drain_outbox(pools, now=moment, execute=execute)
    except Exception:
        _logger.exception("outbox-drain tick failed; scheduler continues")
        return None


async def plan_active_connections(pools: WorkerPools) -> list[uuid.UUID]:
    """The ids of every active (non-revoked) external connection, ACROSS every business.

    The planning half of the refresh, and the reason it needs the bypass: *which* businesses have a
    connected calendar is the ANSWER, not the question. Under RLS with no GUC this returns zero rows
    — and the tick reports ``refreshed=0``, in silence, for ever.

    It returns **ids**, deliberately, and not ORM rows: the rows are re-read on the exec pool under
    their own business, so nothing loaded with the bypass ever reaches the execution path.
    """
    async with pools.scan_session(BypassReason.PLAN_CALENDARS) as session:
        rows = await session.scalars(
            select(ExternalConnection.id).where(ExternalConnection.revoked_at.is_(None))
        )
        return list(rows.all())


async def refresh_all_busy_caches(
    pools: WorkerPools,
    *,
    now: datetime,
    window: TimeInterval,
    service_factory: ServiceFactory,
) -> int:
    """Refresh the busy cache for every active connection over ``window`` (RF-12).

    .. rubric:: ==Rewritten: plan → bind per item → execute → settle. And deliberately NO claim.==

    This used to be ONE transaction holding a cross-business ``SELECT``, with a loop inside it that
    **decrypted each business's Google credential and called Google over the network**. Under RLS
    that shape is not merely wrong, it is invisible: the select returns zero rows on the app role,
    the loop runs zero times, the tick reports ``refreshed=0``, and every calendar on the instance
    goes stale without one line of error.

    So it is now three steps with three different authorities:

    1. **plan**, on the bypass pool — the ids of the active connections across every business,
       because that is the one thing which cannot be known per-business in advance;
    2. **bind, per connection**, on the app pool — one ``tenant_scope`` each, so the credential is
       decrypted and the calendar is called with exactly that business's authority and no other;
    3. **settle**, inside that same bound transaction.

    ==There is deliberately NO claim.== The claim/lease pattern exists in the outbox because
    ``Outbox`` has the COLUMNS to hold it (``claimed_by``, ``lease_expires_at``).
    ``ExternalConnection`` has neither, so demanding one here would mean new DDL, a lease and a
    recovery pass — and this batch has to stay short and featureless, which is exactly what lets
    every other wave run in parallel behind it. It is safe without one because the two facts it
    rests
    on are already true and already documented: the scheduler runs in **exactly one process**
    (``deploy/README.md``), and the refresh is idempotent. ==The change is the pool and the GUC, not
    the claim.==

    A failure in one connection is isolated to it and logged; the rest of the batch continues.
    Returns how many refreshed successfully.
    """
    connection_ids = await plan_active_connections(pools)

    refreshed = 0
    for connection_id in connection_ids:
        try:
            if await _refresh_one_busy_cache(
                pools,
                connection_id,
                now=now,
                window=window,
                service_factory=service_factory,
            ):
                refreshed += 1
        except Exception:
            _logger.exception(
                "busy-cache refresh failed for connection %s; continuing", connection_id
            )
    return refreshed


async def _refresh_one_busy_cache(
    pools: WorkerPools,
    connection_id: uuid.UUID,
    *,
    now: datetime,
    window: TimeInterval,
    service_factory: ServiceFactory,
) -> bool:
    """Refresh ONE connection, under ITS OWN business. Returns whether it actually refreshed.

    The connection's business is read on the bypass pool (it is the single fact this cannot know
    yet), and then everything real — the row itself, the credential, the cache writes — is re-read
    and written on the app pool inside that business's ``tenant_scope``. Nothing loaded under the
    bypass crosses into the execution: only the two ids do.
    """
    async with pools.scan_session(BypassReason.PLAN_CALENDARS) as scan:
        tenant_id = await scan.scalar(
            select(ExternalConnection.tenant_id).where(ExternalConnection.id == connection_id)
        )
    if tenant_id is None:
        # Revoked and deleted between the plan and now. Nothing to do, and not an error.
        return False

    with tenant_scope(tenant_id):
        async with pools.exec_maker() as session, session.begin():
            connection = await session.get(ExternalConnection, connection_id)
            if connection is None or connection.revoked_at is not None:
                return False
            service = service_factory(connection)
            await refresh_busy_cache(
                session, connection=connection, window=window, now=now, service=service
            )
            return True


async def run_busy_refresh_once(
    *,
    pools: WorkerPools,
    service_factory: ServiceFactory,
    now: datetime | None = None,
    horizon: timedelta = BUSY_REFRESH_HORIZON,
) -> int | None:
    """One busy-cache refresh pass over all active connections. Returns the number refreshed, or
    ``None`` if the whole tick failed (logged) — never propagates.
    """
    moment = now if now is not None else datetime.now(UTC)
    window = TimeInterval(start=moment, end=moment + horizon)
    try:
        return await refresh_all_busy_caches(
            pools, now=moment, window=window, service_factory=service_factory
        )
    except Exception:
        _logger.exception("busy-refresh tick failed; scheduler continues")
        return None


# --------------------------------------------------------------------------------------
# Live wiring — reads app.state effects + builds the real AsyncIOScheduler. Never run offline.
# --------------------------------------------------------------------------------------


def _decryption_fernet(app: FastAPI) -> MultiFernet:
    """The MultiFernet a live tick decrypts with: the current key first, and the retiring one during
    a rotation. ``app.state.fernet_keys`` is ``Settings.decryption_fernet_keys()`` — so a row the
    rotation has not reached yet, still on the old key, is readable throughout the window."""
    return MultiFernet([Fernet(key) for key in app.state.fernet_keys])


def build_webhook_deliverer(app: FastAPI) -> WebhookDeliverer:
    """The live webhook-delivery pass, built from the WORKER app's state — ==and TESTED.==

    Extracted out of the ``# pragma: no cover`` tick for the reason :func:`build_drain_executor`
    was: the seam between what the boot PUTS on ``app.state`` and what a tick READS back off it is
    where a silent break hides. ==And it is not hypothetical.== While writing the drain's version of
    this test, its author found that an earlier edit of his own had eaten ``app.state.fernet_keys``
    out of the worker's boot — the name ==all three ticks read==, and this one signs every envelope
    with it. The worker would have booted clean, reported healthy for ever, and delivered nothing.

    Each of the FOUR names below is read inside a tick body that catches and logs, so losing any of
    them is an inert worker with green health checks and not one line of error. Reading them in a
    function a test can call against a really-booted lifespan is what makes that day loud.

    ``app`` is the ``aethercal-worker`` process's app, never the web one — the web has no ``pools``
    on its state at all, because it holds no engine with ``BYPASSRLS``.

    ``webhook_allowlist`` is put on the state eagerly by the worker factory, so the tick cannot fall
    back to "nothing allowed" for an instance whose operator DID declare their network.
    """
    return functools.partial(
        run_webhook_delivery_once,
        pools=app.state.pools,
        http_client=app.state.http_client,
        # The ROTATION READER — current key, plus the retiring one during a rotation — so a
        # subscription created before the rotation reached it stays signable across the window.
        fernet_key=app.state.fernet_keys,
        allowlist=app.state.webhook_allowlist,
    )


def make_webhook_delivery_tick(app: FastAPI) -> Tick:
    """Bind a webhook-delivery tick to the worker's state.

    Thin on purpose: every decision it makes lives in :func:`build_webhook_deliverer`, which is
    tested against a really-booted lifespan. What is left here is the call itself.

    ==And NOT ``# pragma: no cover``, though it used to be.== Nothing here is unreachable offline:
    the tick touches the network only through seams a test already replaces, and
    ``tests/test_worker_tick_wiring.py`` drives this exact closure. The pragma was covering a line
    that could have called the wrong builder — a copy-paste that ticks happily for ever and delivers
    nothing, which is precisely the class of bug this module keeps paying for.
    """

    async def _tick() -> None:
        await build_webhook_deliverer(app)()

    return _tick


def build_busy_refresher(app: FastAPI) -> BusyRefresher:
    """The live busy-cache refresh pass, built from the worker's state — ==and TESTED.==

    The same extraction, for the same reason (see :func:`build_webhook_deliverer`). This tick's
    silence is the quietest of the three: a refresh that never runs does not fail a booking, it lets
    one be double-booked weeks later against a calendar nobody noticed had gone stale.
    """
    # The ROTATION READER: a MultiFernet over the current key (and the retiring one during a
    # rotation), so a Google token stored before the rotation reached it is still decryptable.
    fernet = _decryption_fernet(app)
    service_factory: ServiceFactory = functools.partial(build_live_service, fernet=fernet)
    return functools.partial(
        run_busy_refresh_once, pools=app.state.pools, service_factory=service_factory
    )


def make_busy_refresh_tick(app: FastAPI) -> Tick:
    """Bind a busy-cache refresh tick to the worker's state.

    Thin on purpose: every decision it makes lives in :func:`build_busy_refresher`. Not
    ``# pragma: no cover`` either, and for the same reason as :func:`make_webhook_delivery_tick`.
    """

    async def _tick() -> None:
        await build_busy_refresher(app)()

    return _tick


def _payment_confirm_effects(app: FastAPI):
    """The arbiter's ``confirm_effects`` for the parked tick: the same chain a free booking runs.

    Built here in the worker, the one place that may hold both the booking service and the arbiter.
    """
    settings = app.state.settings
    base = (settings.booking_base_url or "http://localhost").rstrip("/")
    effects = BookingEffects(signer=GuestTokenSigner(settings.app_secret), booking_base_url=base)

    async def _run(session: Any, booking: Booking, now: Any) -> None:
        await confirm_paid_booking_effects(session, booking=booking, effects=effects, now=now)

    return _run


async def resolve_senders_for(app: FastAPI, tenant_id: uuid.UUID) -> TenantSenders:
    """Open a short, tenant-bound session and resolve THAT business's senders (B-03bis).

    ==On ``pools.exec_maker``, the APP-role pool, inside the drain's per-item ``tenant_scope``.==
    Both halves matter: the GUC listener stamps the business onto this transaction, so the
    credential row is read under RLS with exactly that business bound — and ``_row_for``'s own
    ``tenant_id`` filter is the second, independent reason another business's row cannot come back.

    The session is opened and closed around the read alone. R8 forbids holding a transaction across
    the network I/O that follows, and this is a pure read.

    .. rubric:: ==NOT ``# pragma: no cover``, and that is the point==

    This is the live glue between the worker's BOOT and the funnel: the one place
    ``app.state.instance_sender_defaults`` is read. If the boot ever stops writing that name, every
    drain tick dies here with an ``AttributeError`` — and no amount of unit-testing the funnel would
    notice, because the funnel would still be perfect. A pragma here buys the green without buying
    the truth.

    It reaches no network (building a sender opens no socket), so there is nothing to excuse it
    from coverage with. ``tests/test_worker_sender_wiring.py`` drives it against an app state built
    by the worker's OWN lifespan.
    """
    pools: WorkerPools = app.state.pools
    async with pools.exec_maker() as session:
        return await resolve_tenant_senders(
            session,
            tenant_id=tenant_id,
            # The ROTATION READER — the current key plus the retiring one — so a credential the
            # rotation has not reached yet still sends throughout the window.
            fernet_key=app.state.fernet_keys,
            defaults=app.state.instance_sender_defaults,
            clients=app.state.sender_clients,
        )


def build_drain_executor(app: FastAPI) -> OutboxExecutor:
    """The live outbox ``execute``, built from the worker's app state — ==and TESTED.==

    Extracted out of the ``# pragma: no cover`` tick on purpose. The seam between *what the boot
    puts on* ``app.state`` and *what the drain reads back off it* is exactly where a silent break
    hides: an ``AttributeError`` on a name nobody typed twice is invisible until every reminder on
    the instance has already stopped going out.

    ==Built over ``pools.exec_maker``, the APP-role pool, on purpose.== The handlers read the
    booking, decrypt the business's credential and write the ledger, and every one of those is a
    tenant-scoped table. They run inside the drain's per-item ``tenant_scope``, so under RLS they
    see exactly that item's business — which is the whole point of splitting the pools: the one
    place a cross-business leak would be *externally visible* is precisely here, in the process that
    sends the guest's name and address to somebody's webhook.

    ==The senders are RESOLVED PER ITEM, never bound here (B-03bis).== This used to hand the
    executor ``app.state.email_sender`` and ``app.state.channel_senders`` — one SMTP relay and one
    WhatsApp number, read from the instance's environment at boot and shared by every business the
    drain worked through. That is how a business's reminder went out from the operator's number. The
    executor now takes a resolver and asks it for each item's own business, by name.

    The same reasoning armed the MONEY path (B-05b): being extracted here is what makes it
    verifiable that the REFUND runner really carries the worker's gateway and rotation keys, and is
    invocable. A path nobody exercises is not acceptable, whatever a static read of it concludes.
    """
    pools: WorkerPools = app.state.pools
    # The ROTATION READER (see build_busy_refresher): reads a credential under EITHER key during a
    # rotation, so the drain never fails to decrypt a row the rotation has not reached.
    fernet = _decryption_fernet(app)
    service_factory: ServiceFactory = functools.partial(build_live_service, fernet=fernet)
    # ==The money runners (B-05b), fail-closed (finding 2).== Read the gateways + rotation keys
    # DEFENSIVELY off app state (a missing one must not AttributeError), and let
    # ``build_money_runners`` decide: the REFUND runner needs BOTH, EXPIRE_HOLD needs neither. With
    # either missing the refund runner is None and a REFUND intent raises loudly, not silently
    # stranding a guest's money.
    fernet_keys = getattr(app.state, "fernet_keys", None)
    gateways = getattr(app.state, "payment_gateways", None)
    refund_runner, expire_hold_runner = build_money_runners(
        exec_maker=pools.exec_maker, gateways=gateways, fernet_keys=fernet_keys
    )
    return make_booking_effect_executor(
        sessionmaker=pools.exec_maker,
        resolve_senders=functools.partial(resolve_senders_for, app),
        service_factory=service_factory,
        refund_runner=refund_runner,
        expire_hold_runner=expire_hold_runner,
    )


def build_parked_payment_runner(app: FastAPI) -> ParkedPaymentRunner:
    """The live parked-payment pass, built from the worker's state — ==and TESTED (B-09).==

    The same extraction as :func:`build_drain_executor` / :func:`build_busy_refresher`, and the one
    B-09 left for last because it was the lowest-risk. The seam it protects:
    :func:`_payment_confirm_effects` reads ``app.state.settings`` (the booking base URL and the
    guest signer), and this reads ``app.state.pools``. Both are put on the state by the boot; if it
    ever stops, this pass dies with an ``AttributeError`` on a name nobody typed twice, and every
    payment event that beat its checkout stops being reconciled — invisibly. Read here, out of the
    pragma'd tick, ``tests/test_worker_tick_wiring.py`` drives it against the REAL lifespan and
    catches that.

    The pass itself (criterion 29): re-run the arbiter for events that beat their checkout's commit,
    dead-letter the ones that never resolve. It swallows its own failures (like every guarded tick)
    so a bad pass never kills the loop.
    """
    pools: WorkerPools = app.state.pools
    confirm_effects = _payment_confirm_effects(app)

    async def _run() -> None:
        try:
            await run_parked_payment_tick(
                pools, now=datetime.now(UTC), confirm_effects=confirm_effects
            )
        except Exception:
            _logger.exception("parked-payment tick failed; scheduler continues")

    return _run


def make_outbox_drain_tick(app: FastAPI) -> Tick:
    """Bind an outbox-drain tick to the worker's state.

    Thin on purpose: every decision it makes lives in :func:`build_drain_executor` (the drain) and
    :func:`build_parked_payment_runner` (the parked-payment pass), both TESTED against the real
    lifespan. Not ``# pragma: no cover`` any more — B-09's last residual was the boot-seam read that
    used to hide in here, and it now lives in a build function a test drives, same as its two
    siblings.
    """

    async def _tick() -> None:
        await run_outbox_drain_once(pools=app.state.pools, execute=build_drain_executor(app))
        await build_parked_payment_runner(app)()

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
    "BusyRefresher",
    "ParkedPaymentRunner",
    "SchedulerLike",
    "Tick",
    "WebhookDeliverer",
    "build_busy_refresher",
    "build_drain_executor",
    "build_interval_scheduler",
    "build_parked_payment_runner",
    "build_webhook_deliverer",
    "make_busy_refresh_tick",
    "make_outbox_drain_tick",
    "make_webhook_delivery_tick",
    "refresh_all_busy_caches",
    "register_scheduler_jobs",
    "resolve_senders_for",
    "run_busy_refresh_once",
    "run_outbox_drain_once",
    "run_webhook_delivery_once",
    "start_scheduler",
    "stop_scheduler",
]
