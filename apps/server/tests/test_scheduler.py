"""Offline tests for the background-scheduler wiring (F1-12 / RF-19).

The live APScheduler object is held behind an ``Any`` seam and never started here. What IS tested:

* the *wiring* — ``register_scheduler_jobs`` / ``start_scheduler`` / ``stop_scheduler`` drive a
  FAKE scheduler, so we assert exactly which jobs (ids), at which intervals, get registered and that
  start/stop is invoked;
* the *guarded ticks* — each job body opens its own session and swallows a failure so one bad tick
  never kills the loop (``run_webhook_delivery_once``, ``run_busy_refresh_once``);
* the *per-connection busy refresh* — ``refresh_all_busy_caches`` refreshes every active connection
  and skips a failing one without stopping the rest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from aethercal.core.model import TimeInterval
from aethercal.server import scheduler as sched
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import BusyCache, ExternalConnection, Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.scheduler import (
    BUSY_REFRESH_JOB_ID,
    DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS,
    DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS,
    DEFAULT_WEBHOOK_INTERVAL_SECONDS,
    OUTBOX_DRAIN_JOB_ID,
    WEBHOOK_DELIVERY_JOB_ID,
    refresh_all_busy_caches,
    register_scheduler_jobs,
    run_busy_refresh_once,
    run_outbox_drain_once,
    run_webhook_delivery_once,
    start_scheduler,
    stop_scheduler,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.webhooks.allowlist import NO_PRIVATE_TARGETS, PrivateTargetAllowlist
from aethercal.server.webhooks.delivery import DeliveryReport


def _pools(maker: async_sessionmaker[AsyncSession]) -> WorkerPools:
    """Both of the worker pools over ONE offline sessionmaker.

    Every tick takes :class:`WorkerPools` now, not a sessionmaker: on PostgreSQL each needs a
    ``BYPASSRLS`` connection to FIND work whose business it cannot know until it has read the row,
    and the app role to EXECUTE that work under row-level security, bound to that row own business.
    SQLite has neither roles nor RLS, so here the two collapse back into one.
    """
    return WorkerPools.for_offline_tests(maker)


def _pools(maker: async_sessionmaker[AsyncSession]) -> WorkerPools:
    """Both of the worker pools over ONE offline sessionmaker.

    Every tick takes :class:`WorkerPools` now, not a sessionmaker: on PostgreSQL each needs a
    ``BYPASSRLS`` connection to FIND work whose business it cannot know until it has read the row,
    and the app role to EXECUTE that work under row-level security, bound to that row own business.
    SQLite has neither roles nor RLS, so here the two collapse back into one.
    """
    return WorkerPools.for_offline_tests(maker)


TenantFactory = Callable[..., Awaitable[Tenant]]

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# A fake scheduler recording every registration + lifecycle call (no APScheduler).
# --------------------------------------------------------------------------------------


@dataclass
class _RegisteredJob:
    func: Callable[..., Any]
    trigger: str
    seconds: int
    job_id: str
    replace_existing: bool


@dataclass
class FakeScheduler:
    jobs: list[_RegisteredJob] = field(default_factory=list)
    started: bool = False
    shutdown_calls: list[bool] = field(default_factory=list)

    def add_job(
        self,
        func: Callable[..., Any],
        *,
        trigger: str,
        seconds: int,
        id: str,
        replace_existing: bool,
    ) -> None:
        self.jobs.append(
            _RegisteredJob(
                func=func,
                trigger=trigger,
                seconds=seconds,
                job_id=id,
                replace_existing=replace_existing,
            )
        )

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


async def _noop() -> None:
    return None


# --------------------------------------------------------------------------------------
# A fake Google service (empty freebusy) + a factory over it.
# --------------------------------------------------------------------------------------


class _FakeExecute:
    def execute(self) -> dict[str, Any]:
        return {"calendars": {"primary": {"busy": []}}}


class _FakeFreebusy:
    def query(self, *, body: Any) -> _FakeExecute:
        return _FakeExecute()


class _FakeGoogleService:
    def freebusy(self) -> _FakeFreebusy:
        return _FakeFreebusy()


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory aiosqlite sessionmaker with the full schema (jobs open their own session)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


async def _connect(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    fernet: Fernet,
    email: str,
) -> ExternalConnection:
    tenant = await tenant_factory(session, email=email)
    user = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        credential=GoogleCredential(account_email=email, token_json='{"token": "at"}'),
        fernet=fernet,
    )
    await session.flush()
    return connection


# --------------------------------------------------------------------------------------
# 1. Wiring — which jobs, which intervals, start/stop.
# --------------------------------------------------------------------------------------


def test_register_scheduler_jobs_registers_all_interval_jobs() -> None:
    scheduler = FakeScheduler()

    register_scheduler_jobs(
        scheduler, webhook_tick=_noop, busy_refresh_tick=_noop, outbox_tick=_noop
    )

    by_id = {job.job_id: job for job in scheduler.jobs}
    assert set(by_id) == {WEBHOOK_DELIVERY_JOB_ID, BUSY_REFRESH_JOB_ID, OUTBOX_DRAIN_JOB_ID}
    webhook = by_id[WEBHOOK_DELIVERY_JOB_ID]
    busy = by_id[BUSY_REFRESH_JOB_ID]
    outbox = by_id[OUTBOX_DRAIN_JOB_ID]
    assert webhook.trigger == "interval"
    assert webhook.seconds == DEFAULT_WEBHOOK_INTERVAL_SECONDS == 60
    assert busy.trigger == "interval"
    assert busy.seconds == DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS
    assert outbox.trigger == "interval"
    assert outbox.seconds == DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS == 60
    # Idempotent replace so a restart never double-registers.
    assert webhook.replace_existing is True
    assert busy.replace_existing is True
    assert outbox.replace_existing is True


def test_register_scheduler_jobs_honors_custom_intervals() -> None:
    scheduler = FakeScheduler()

    register_scheduler_jobs(
        scheduler,
        webhook_tick=_noop,
        busy_refresh_tick=_noop,
        outbox_tick=_noop,
        webhook_interval_seconds=15,
        busy_refresh_interval_seconds=120,
        outbox_drain_interval_seconds=30,
    )

    by_id = {job.job_id: job.seconds for job in scheduler.jobs}
    assert by_id[WEBHOOK_DELIVERY_JOB_ID] == 15
    assert by_id[BUSY_REFRESH_JOB_ID] == 120
    assert by_id[OUTBOX_DRAIN_JOB_ID] == 30


def test_start_scheduler_registers_then_starts() -> None:
    scheduler = FakeScheduler()

    start_scheduler(scheduler, webhook_tick=_noop, busy_refresh_tick=_noop, outbox_tick=_noop)

    assert len(scheduler.jobs) == 3
    assert scheduler.started is True


def test_stop_scheduler_shuts_down_without_waiting() -> None:
    scheduler = FakeScheduler()

    stop_scheduler(scheduler)

    assert scheduler.shutdown_calls == [False]


# --------------------------------------------------------------------------------------
# 2. Webhook-delivery tick — a clean pass, and a guarded failing pass.
# --------------------------------------------------------------------------------------


async def test_run_webhook_delivery_once_reports_a_clean_empty_pass(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with httpx.AsyncClient() as http_client:
        report = await run_webhook_delivery_once(
            pools=_pools(sqlite_sessionmaker),
            http_client=http_client,
            fernet_key=derive_fernet_key("test-app-secret"),
            allowlist=NO_PRIVATE_TARGETS,
            now=NOW,
        )

    assert report is not None
    assert report.attempted == 0


async def test_run_webhook_delivery_once_swallows_a_failing_tick(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("delivery blew up")

    monkeypatch.setattr(sched, "deliver_due", _boom)

    async with httpx.AsyncClient() as http_client:
        report = await run_webhook_delivery_once(
            pools=_pools(sqlite_sessionmaker),
            http_client=http_client,
            fernet_key=derive_fernet_key("test-app-secret"),
            allowlist=NO_PRIVATE_TARGETS,
            now=NOW,
        )

    # One bad tick must never propagate — it returns None so the scheduler keeps ticking.
    assert report is None


async def test_the_tick_hands_the_operators_allowlist_to_the_worker(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The wiring itself is a place a silent no-op can hide.==

    An operator declares ``192.168.1.0/24``, the process comes up, and every delivery to their n8n
    still dies — because the tick never passed the allowlist down and the worker fell back to
    "nothing private is allowed". Nothing errors; nothing is delivered. So this asserts the
    EFFECTIVE state: the exact object the caller was given is the object ``deliver_due`` receives.

    (``deliver_due`` also takes it as a required keyword, so pyright refuses to let a call site drop
    it. This test is the belt to that braces, over the one seam types cannot see: ``app.state``.)
    """
    declared = PrivateTargetAllowlist.parse("192.168.1.0/24")
    seen: dict[str, object] = {}

    async def _spy(*_args: Any, **kwargs: Any) -> DeliveryReport:
        seen["allowlist"] = kwargs["allowlist"]
        return DeliveryReport()

    monkeypatch.setattr(sched, "deliver_due", _spy)

    async with httpx.AsyncClient() as http_client:
        await run_webhook_delivery_once(
            pools=_pools(sqlite_sessionmaker),
            http_client=http_client,
            fernet_key=derive_fernet_key("test-app-secret"),
            allowlist=declared,
            now=NOW,
        )

    assert seen["allowlist"] is declared


# --------------------------------------------------------------------------------------
# 2b. Outbox-drain tick — a clean pass, and a guarded failing pass.
# --------------------------------------------------------------------------------------


async def _noop_execute(_session: AsyncSession, _outbox: Any, _now: datetime) -> None:
    return None


async def test_run_outbox_drain_once_reports_a_clean_empty_pass(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    report = await run_outbox_drain_once(
        pools=_pools(sqlite_sessionmaker), execute=_noop_execute, now=NOW
    )

    assert report is not None
    assert report.attempted == 0


async def test_run_outbox_drain_once_swallows_a_failing_tick(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("drain blew up")

    monkeypatch.setattr(sched, "drain_outbox", _boom)

    report = await run_outbox_drain_once(
        pools=_pools(sqlite_sessionmaker), execute=_noop_execute, now=NOW
    )

    # One bad tick must never propagate — it returns None so the scheduler keeps ticking.
    assert report is None


# --------------------------------------------------------------------------------------
# 3. Busy-cache refresh — every active connection, one failure never stops the rest.
# --------------------------------------------------------------------------------------


async def test_refresh_all_busy_caches_refreshes_every_active_connection(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: TenantFactory,
    fernet: Fernet,
) -> None:
    a = await _connect(sqlite_session, tenant_factory, fernet=fernet, email="a@host.com")
    b = await _connect(sqlite_session, tenant_factory, fernet=fernet, email="b@host.com")
    # ==Committed, not merely flushed.== The refresh no longer runs inside the caller's session: it
    # PLANS on the bypass pool and re-reads each connection on the app pool, under that connection's
    # own business. Those are different sessions, so uncommitted rows simply do not exist for them —
    # which is exactly the property that makes the per-item binding possible in the first place.
    await sqlite_session.commit()

    window = TimeInterval(start=NOW, end=NOW + timedelta(days=30))

    refreshed = await refresh_all_busy_caches(
        _pools(sqlite_maker),
        now=NOW,
        window=window,
        service_factory=lambda _conn: _FakeGoogleService(),
    )

    assert refreshed == 2
    # Both connections got their coverage stamp (proof the refresh actually ran per connection).
    for connection in (a, b):
        await sqlite_session.refresh(connection)
        assert connection.busy_synced_at is not None


async def test_refresh_all_busy_caches_skips_a_failing_connection(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: TenantFactory,
    fernet: Fernet,
) -> None:
    good = await _connect(sqlite_session, tenant_factory, fernet=fernet, email="good@host.com")
    bad = await _connect(sqlite_session, tenant_factory, fernet=fernet, email="bad@host.com")
    # ==Committed, not merely flushed.== The refresh no longer runs inside the caller's session: it
    # PLANS on the bypass pool and re-reads each connection on the app pool, under that connection's
    # own business. Those are different sessions, so uncommitted rows simply do not exist for them —
    # which is exactly the property that makes the per-item binding possible in the first place.
    await sqlite_session.commit()

    window = TimeInterval(start=NOW, end=NOW + timedelta(days=30))

    def _factory(connection: ExternalConnection) -> Any:
        if connection.id == bad.id:
            raise RuntimeError("google unreachable for this host")
        return _FakeGoogleService()

    refreshed = await refresh_all_busy_caches(
        _pools(sqlite_maker), now=NOW, window=window, service_factory=_factory
    )

    # The good connection still refreshed; the failing one is skipped, not fatal.
    assert refreshed == 1
    await sqlite_session.refresh(good)
    await sqlite_session.refresh(bad)
    assert good.busy_synced_at is not None
    assert bad.busy_synced_at is None


async def test_refresh_all_busy_caches_isolates_a_flush_error_from_the_rest(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: TenantFactory,
    fernet: Fernet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQLAlchemy error mid-flush on ONE connection must not poison the whole batch.

    ==The SAVEPOINT is gone, and what replaced it is stronger.== This used to be ONE transaction
    over
    every business, with each connection wrapped in a ``begin_nested()`` — because a failing flush
    deactivates the shared transaction, and without that nesting every remaining connection aborted
    with it and the batch half-completed in silence.

    The refresh is no longer one transaction. Under row-level security it CANNOT be: the plan is
    cross-business (only the bypass pool can see whose calendars are connected), while each refresh
    must run bound to its own business, and one binding cannot be two businesses at once. So each
    connection now gets its own session and its own transaction on the app pool. A poisoned flush
    rolls back exactly that one — not a SAVEPOINT inside a shared transaction, but a transaction
    that
    was never shared to begin with.

    The property being asserted is unchanged, and it is the one that matters: one bad connection,
    and
    the rest still refresh.
    """
    await _connect(sqlite_session, tenant_factory, fernet=fernet, email="a@host.com")
    await _connect(sqlite_session, tenant_factory, fernet=fernet, email="b@host.com")
    # Committed, not merely flushed: the refresh opens its own sessions now (see above).
    await sqlite_session.commit()
    window = TimeInterval(start=NOW, end=NOW + timedelta(days=30))

    real_refresh = sched.refresh_busy_cache
    calls = {"count": 0}

    async def _refresh(
        session: AsyncSession,
        *,
        connection: ExternalConnection,
        window: TimeInterval,
        now: datetime,
        service: Any,
    ) -> list[TimeInterval]:
        calls["count"] += 1
        if calls["count"] == 1:
            # A genuine SQLAlchemy error DURING FLUSH: a BusyCache row missing its NOT NULL columns.
            # This deactivates the outer transaction; only a SAVEPOINT rollback recovers it so the
            # next connection still refreshes.
            session.add(BusyCache(tenant_id=connection.tenant_id, connection_id=connection.id))
            await session.flush()
        return await real_refresh(
            session, connection=connection, window=window, now=now, service=service
        )

    monkeypatch.setattr(sched, "refresh_busy_cache", _refresh)

    refreshed = await refresh_all_busy_caches(
        _pools(sqlite_maker),
        now=NOW,
        window=window,
        service_factory=lambda _conn: _FakeGoogleService(),
    )

    # The first connection's flush blew up; the OTHER still refreshed. One poisoned transaction does
    # not abort the batch — because it is not the batch's transaction any more, it is only its own.
    assert refreshed == 1
    async with sqlite_maker() as fresh:
        stamped = [
            connection.busy_synced_at
            for connection in (await fresh.scalars(select(ExternalConnection))).all()
        ]
    assert sum(stamp is not None for stamp in stamped) == 1


async def test_run_busy_refresh_once_swallows_a_failing_tick(
    sqlite_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("refresh blew up")

    monkeypatch.setattr(sched, "refresh_all_busy_caches", _boom)

    result = await run_busy_refresh_once(
        pools=_pools(sqlite_sessionmaker),
        service_factory=lambda _conn: _FakeGoogleService(),
        now=NOW,
    )

    assert result is None
