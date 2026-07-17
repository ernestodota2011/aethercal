"""R9 over HTTP - ==now served by the WORKER, and that move IS the point.==

``GET /metrics`` and the ``GET /health/ready`` that reports the backlog used to live in the WEB
process. Under row-level security they could only ever have reported **zeros** there: every
query in ``collect_metrics`` is cross-business, the web holds ``aethercal_app`` under RLS, and a
scrape carries no business to bind - so the endpoint whose entire job is to make a dead drain
VISIBLE would have answered ``200 OK``, ``outbox.due 0``, for ever, with the queue on fire.

They are served by ``aethercal-worker`` now (``api/operator.py``), which holds the ``BYPASSRLS``
scan pool that makes those numbers true. The app is assembled here by hand over the offline
SQLite sessionmaker - wrapped in ``WorkerPools.for_offline_tests``, because SQLite has no RLS
and so nothing to bypass - which keeps these in the default matrix rather than only in the
Postgres job.

Offline, over the same in-memory SQLite the rest of the service-layer suite uses: the app is
assembled by hand (sessionmaker + settings on ``app.state``) so these run in the default matrix
instead of only in the Postgres job. What they pin is the SECURITY of the endpoint, which is the
part a public repository cannot afford to get wrong:

* an **unconfigured** metrics token does not mean "open" — it means the endpoint is CLOSED. A scrape
  endpoint that quietly serves the world because an operator forgot a variable is the silent no-op,
  aimed at the data of every business on the instance;
* a **tenant's API key does not open it.** These numbers are the OPERATOR's view of the whole
  instance, and a tenant holding a perfectly valid key must still be told no;
* a **too-short** token is a hard configuration error, not a warning nobody reads.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aethercal.core.model import BookingStatus
from aethercal.server.api import health, operator
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    OutboxStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.db.pools import WorkerPools
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.settings import Settings

_TOKEN = "m" * 40
_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

ClientFactory = Callable[..., Awaitable[AsyncClient]]


def _app(maker: async_sessionmaker[AsyncSession], **overrides: Any) -> FastAPI:
    """The WORKER shape: ``state.pools``, and NOT ``state.sessionmaker``.

    ``operator.router`` reads ``app.state.pools``, with no fallback - the web process has no
    pools at all, because it holds no engine with ``BYPASSRLS``. An ``AttributeError`` at boot
    is what turns that into a fact rather than a convention.
    """
    application = FastAPI()
    application.state.pools = WorkerPools.for_offline_tests(maker)
    application.state.settings = Settings(
        database_url="postgresql://unused/db", app_secret="test-app-secret", **overrides
    )
    application.include_router(operator.router, prefix="/api/v1")
    return application


def _web_app(maker: async_sessionmaker[AsyncSession]) -> FastAPI:
    """The WEB health surface - which deliberately no longer carries the backlog."""
    application = FastAPI()
    application.state.sessionmaker = maker
    application.include_router(health.router, prefix="/api/v1")
    return application


@pytest_asyncio.fixture
async def client_factory(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[ClientFactory]:
    """Build an app + client per test, with the settings that test is about."""
    opened: list[AsyncClient] = []

    async def _make(**overrides: Any) -> AsyncClient:
        application = _app(sqlite_maker, **overrides)
        http = AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver")
        http.app = application  # type: ignore[attr-defined]  # the test may swap app.state
        opened.append(http)
        return http

    yield _make
    for http in opened:
        await http.aclose()


async def _seed(maker: async_sessionmaker[AsyncSession]) -> Tenant:
    """One tenant, one no-show booking, one DEAD outbox intent."""
    async with maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Acme")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="H", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=_NOW,
            end_at=_NOW + timedelta(minutes=30),
            status=BookingStatus.NO_SHOW,
            # A no-show was confirmed once, so it carries the stamp (B-05a).
            confirmed_at=_NOW - timedelta(days=1),
            guest_name="Ada Lovelace",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        session.add(
            Outbox(
                tenant_id=tenant.id,
                booking_id=booking.id,
                effect=OutboxEffect.EMAIL.value,
                dedupe_key="email:confirmation",
                payload={},
                status=OutboxStatus.DEAD.value,
                attempts=6,
            )
        )
        return tenant


# --------------------------------------------------------------------------------------
# /metrics — the door.
# --------------------------------------------------------------------------------------


async def test_metrics_with_no_token_configured_is_closed_not_open(
    client_factory: ClientFactory,
) -> None:
    """==Fail-closed.== "The operator did not set a token" must never resolve to "serve the outbox
    and the booking counts of every business on this instance to whoever asks". Unconfigured is OFF:
    it answers 503 and says why, rather than quietly becoming a public endpoint."""
    client = await client_factory()

    resp = await client.get("/api/v1/metrics")

    assert resp.status_code == 503
    assert "AETHERCAL_METRICS_TOKEN" in resp.json()["detail"]["message"]


async def test_metrics_without_a_token_is_unauthorized(client_factory: ClientFactory) -> None:
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get("/api/v1/metrics")

    assert resp.status_code == 401


async def test_metrics_with_the_wrong_token_is_unauthorized(client_factory: ClientFactory) -> None:
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {'x' * 40}"})

    assert resp.status_code == 401


async def test_a_tenants_api_key_does_not_open_the_metrics_endpoint(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    """==The isolation that matters here.== These numbers are the OPERATOR's view of the whole
    instance. A tenant holding a perfectly valid API key is still not the operator, and handing them
    the instance-wide backlog and booking counts would leak the other businesses' volume."""
    tenant = await _seed(sqlite_maker)
    async with sqlite_maker() as session, session.begin():
        _, api_key = await issue_api_key(session, tenant_id=tenant.id, name="tenant-key")
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {api_key}"})

    assert resp.status_code == 401


async def test_metrics_with_the_operator_token_serves_prometheus_text(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    tenant = await _seed(sqlite_maker)
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert 'aethercal_outbox_intents{status="dead"} 1' in body
    assert 'aethercal_bookings{status="no_show"} 1' in body
    # And still nothing that says WHOSE.
    assert tenant.slug not in body
    assert "ada@example.com" not in body


async def test_metrics_summary_with_the_operator_token_serves_the_human_report(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    """The human-readable twin: the operator running the shadow reads the same numbers as a person,
    and it leaks no more than the Prometheus text does."""
    tenant = await _seed(sqlite_maker)
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get(
        "/api/v1/metrics/summary", headers={"Authorization": f"Bearer {_TOKEN}"}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    # The operator-facing sections a shadow run reads.
    assert "Bookings" in body
    assert "Outbox" in body
    assert "Payments" in body
    assert "dead-man" in body  # the health line is named, not buried
    # And, exactly like /metrics, it says the numbers but never WHOSE.
    assert tenant.slug not in body
    assert "ada@example.com" not in body


async def test_metrics_summary_without_a_token_is_unauthorized(
    client_factory: ClientFactory,
) -> None:
    """Same guard as /metrics: it is every business's volume side by side, so a scrape with no
    operator token is refused, never served."""
    client = await client_factory(metrics_token=_TOKEN)

    resp = await client.get("/api/v1/metrics/summary")

    assert resp.status_code == 401


async def test_a_non_ascii_bearer_token_is_401_not_500(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    """==A 500 on the observability endpoint is the worst 500 available.==

    ``secrets.compare_digest`` REFUSES to compare non-ASCII ``str``: it raises ``TypeError``. So a
    bearer token carrying one accented character crashed the comparison, and the surface whose whole
    job is to TELL YOU SOMETHING IS WRONG answered 500 — from inside its own auth check, to an
    unauthenticated caller. An operator debugging an outage would find their instruments broken by
    the very request they made to read them.

    A wrong token is a wrong token, whatever alphabet it is written in: 401.
    """
    await _seed(sqlite_maker)
    client = await client_factory(metrics_token=_TOKEN)

    # BYTES, because httpx itself refuses to ascii-encode a non-ASCII header value — which is
    # precisely why this reaches the server as raw bytes in the wild, and why the handler must cope.
    # ASGI carries headers as bytes and Starlette decodes them latin-1, so the token arrives at the
    # comparison as a non-ASCII `str`: exactly the input that used to raise TypeError.
    resp = await client.get(
        "/api/v1/metrics",
        headers={"Authorization": ("Bearer " + "m" * 39 + "é").encode()},
    )

    assert resp.status_code == 401


def test_a_token_too_short_to_be_a_secret_is_refused_at_boot() -> None:
    """A configured-but-weak token is not a degraded feature — it is a hole with the light left on.
    It fails LOUDLY, at construction, rather than guarding the endpoint with something guessable."""
    with pytest.raises(ValueError, match="AETHERCAL_METRICS_TOKEN"):
        Settings(database_url="postgresql://unused/db", app_secret="s", metrics_token="short")


def test_a_non_ascii_configured_token_is_refused_at_boot() -> None:
    """==Fail-closed, exactly like every other misconfiguration of this token.==

    Long enough, but not ASCII. It sails past the length check and then stands in front of the
    endpoint as a secret that ``compare_digest`` cannot compare at all — a "guard" nobody can ever
    present correctly is not a guard, it is an outage lying in wait for the day somebody needs the
    metrics.

    Non-ASCII secrets are a homoglyph trap besides: two tokens that render identically in a terminal
    do not compare equal. A token is bytes-with-a-keyboard. Checked here, at boot, rather than
    discovered at 3am."""
    with pytest.raises(ValueError, match="AETHERCAL_METRICS_TOKEN"):
        Settings(
            database_url="postgresql://unused/db",
            app_secret="s",
            metrics_token="m" * 39 + "é",
        )


def test_an_empty_token_reads_as_unset_rather_than_as_a_password() -> None:
    """``AETHERCAL_METRICS_TOKEN=`` in an env file is a blank an operator left, not a secret. It
    disables the endpoint (which is then closed); it must never become a token that some empty or
    whitespace header happens to match."""
    settings = Settings(database_url="postgresql://unused/db", app_secret="s", metrics_token="   ")

    assert settings.metrics_token is None


# --------------------------------------------------------------------------------------
# /health/ready — liveness was never readiness.
# --------------------------------------------------------------------------------------


async def test_liveness_still_answers_without_touching_the_database(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory()

    resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_reports_the_backlog(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    """``/health`` proves the PROCESS is up — it opens no connection, so it stays green while the
    database is on fire. Readiness is the one that actually asks."""
    await _seed(sqlite_maker)
    client = await client_factory()

    resp = await client.get("/api/v1/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "up"
    assert body["outbox"]["dead"] == 1
    assert body["outbox"]["pending"] == 0
    assert body["outbox"]["due"] == 0
    assert body["outbox"]["oldest_due_age_seconds"] == 0


async def test_readiness_is_503_when_the_database_is_unreachable(
    client_factory: ClientFactory,
) -> None:
    """The whole point of a readiness probe: it must FAIL when the thing it depends on is gone. A
    probe that reports ready no matter what reports nothing at all.

    The engine here is pointed at a database that genuinely cannot be opened, and the endpoint has
    to survive the real driver error — the EFFECTIVE state, not a mock of it. (A closed TCP port
    would do the same job, but it costs minutes of connect-timeout on Windows and this must stay a
    test anybody runs.)
    """
    client = await client_factory()
    dead_engine = create_async_engine("sqlite+aiosqlite:////nonexistent-directory/aethercal.db")
    client.app.state.pools = WorkerPools.for_offline_tests(  # type: ignore[attr-defined]
        async_sessionmaker(dead_engine)
    )

    resp = await client.get("/api/v1/health/ready")

    assert resp.status_code == 503
    body = resp.json()["detail"]
    assert body["status"] == "degraded"
    assert body["database"] == "down"
    await dead_engine.dispose()


async def test_readiness_carries_no_per_business_data(
    sqlite_maker: async_sessionmaker[AsyncSession], client_factory: ClientFactory
) -> None:
    """It is unauthenticated (a container healthcheck carries no credentials), so it may report only
    instance-wide operational counts — never a tenant, never a guest."""
    tenant = await _seed(sqlite_maker)
    client = await client_factory()

    resp = await client.get("/api/v1/health/ready")

    assert tenant.slug not in resp.text
    assert "ada@example.com" not in resp.text


# --------------------------------------------------------------------------------------
# ==Criterion 12c - the WEB /health/ready lost the backlog. The second dead-man switch.==
# --------------------------------------------------------------------------------------


async def test_the_webs_readiness_no_longer_serves_the_backlog(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==It was very nearly left behind, lying in green.==

    ``GET /health/ready`` on the WEB process is unauthenticated (a container healthcheck carries no
    credentials), so it can hold no tenant authority and bind no business. It used to call the same
    cross-business ``collect_metrics`` as ``/metrics`` - which fills with zeros by construction and
    never raises. Under RLS that reads:

        ``status: "ready"``, ``database: "up"``, ``outbox.due = 0`` - for ever, with the outbox on
        fire, on the very probe the deployment uses to decide this instance is healthy.

    ``/metrics`` was rescued by moving it to the worker. This one would have stayed exactly where it
    was. So the web keeps only the half it can answer truthfully - *can I reach the database* - and
    the backlog goes to the worker, where a bypass pool exists to measure it.
    """
    await _seed(sqlite_maker)
    application = _web_app(sqlite_maker)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as http:
        resp = await http.get("/api/v1/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ready", "database": "up"}
    assert "outbox" not in body, (
        "the web process cannot report the backlog truthfully - under RLS it would report zeros"
    )


async def test_the_webs_readiness_still_fails_loudly_when_the_database_is_gone() -> None:
    """Losing the backlog must not cost the probe its ONE honest property: it still has to BREAK.

    ``SELECT 1`` was chosen precisely because it raises when the database is unreachable - which is
    exactly what the zero-filling backlog block could never do.
    """
    dead_engine = create_async_engine("sqlite+aiosqlite:////nonexistent-directory/aethercal.db")
    application = _web_app(async_sessionmaker(dead_engine))
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as http:
        resp = await http.get("/api/v1/health/ready")

    assert resp.status_code == 503
    assert resp.json()["detail"]["database"] == "down"
    await dead_engine.dispose()
