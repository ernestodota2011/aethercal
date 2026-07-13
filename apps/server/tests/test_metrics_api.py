"""R9 over HTTP: ``GET /metrics`` (authenticated) and ``GET /health/ready`` (the backlog).

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
from aethercal.server.api import health, metrics
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    OutboxStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.settings import Settings

_TOKEN = "m" * 40
_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

ClientFactory = Callable[..., Awaitable[AsyncClient]]


def _app(maker: async_sessionmaker[AsyncSession], **overrides: Any) -> FastAPI:
    application = FastAPI()
    application.state.sessionmaker = maker
    application.state.settings = Settings(
        database_url="postgresql://unused/db", app_secret="test-app-secret", **overrides
    )
    application.include_router(metrics.router, prefix="/api/v1")
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


def test_a_token_too_short_to_be_a_secret_is_refused_at_boot() -> None:
    """A configured-but-weak token is not a degraded feature — it is a hole with the light left on.
    It fails LOUDLY, at construction, rather than guarding the endpoint with something guessable."""
    with pytest.raises(ValueError, match="AETHERCAL_METRICS_TOKEN"):
        Settings(database_url="postgresql://unused/db", app_secret="s", metrics_token="short")


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
    client.app.state.sessionmaker = async_sessionmaker(dead_engine)  # type: ignore[attr-defined]

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
