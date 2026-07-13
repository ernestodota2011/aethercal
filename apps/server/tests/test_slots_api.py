"""End-to-end slots endpoint through the real app over PostgreSQL (F1-04, RF-03/RF-16).

These are ``db``-marked (whole module): they need a real server (``AETHERCAL_TEST_DATABASE_URL``),
skip in the offline matrix, and run in CI's ``test-db`` job. They are the executable spec for the
``GET /slots`` HTTP contract: auth, error mapping (404 unknown event type, 422 bad tz / inverted
range), and a happy path that returns UTC slots with the requested display timezone echoed back.

``wired_client`` mounts the feature router directly on the app (the orchestrator wires it under
``/api/v1`` in production; here the path is ``/slots/``). ``seeded`` provisions a tenant with a
host, a weekly Mon-Fri 09:00-17:00 UTC schedule, an event type, and its API key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.api import slots
from aethercal.server.db.models import EventType, Schedule, Tenant, User
from aethercal.server.services.api_keys import issue_api_key

pytestmark = pytest.mark.db

SLOTS = "/slots/"
_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(slots.router)
    return client


@pytest_asyncio.fixture
async def seeded(app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Seed a tenant + host + schedule + event type + API key; return the headers and ids."""
    # ==Seeded on the OWNER engine.== Under FORCE ROW LEVEL SECURITY these rows carry a
    # business nothing has bound yet, so the WITH CHECK refuses them on the app role. The
    # REQUEST is what is under test, and it binds its own business from the key below.
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Seeded Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(
            tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5
        )
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro Call",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()
        _, full_key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")
        event_type_id = str(event_type.id)
    return {
        "headers": {"Authorization": f"Bearer {full_key}"},
        "event_type_id": event_type_id,
    }


def _window() -> dict[str, str]:
    """A future weekday-spanning window (within max_advance) so the happy path yields slots."""
    today = datetime.now(UTC).date()
    return {
        "from": (today + timedelta(days=2)).isoformat(),
        "to": (today + timedelta(days=9)).isoformat(),
    }


async def test_requires_auth(wired_client: AsyncClient) -> None:
    resp = await wired_client.get(
        SLOTS, params={"event_type": str(uuid.uuid4()), "tz": "UTC", **_window()}
    )
    assert resp.status_code == 401


async def test_unknown_event_type_returns_404(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.get(
        SLOTS,
        params={"event_type": str(uuid.uuid4()), "tz": "UTC", **_window()},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


async def test_bad_timezone_returns_422(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.get(
        SLOTS,
        params={"event_type": str(uuid.uuid4()), "tz": "Not/AZone", **_window()},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_timezone"


async def test_inverted_date_range_returns_422(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    today = datetime.now(UTC).date()
    resp = await wired_client.get(
        SLOTS,
        params={
            "event_type": str(uuid.uuid4()),
            "tz": "UTC",
            "from": (today + timedelta(days=9)).isoformat(),
            "to": (today + timedelta(days=2)).isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_range"


async def test_window_larger_than_cap_returns_422(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    today = datetime.now(UTC).date()
    resp = await wired_client.get(
        SLOTS,
        params={
            "event_type": str(uuid.uuid4()),
            "tz": "UTC",
            "from": today.isoformat(),
            "to": (today + timedelta(days=slots.MAX_QUERY_DAYS + 1)).isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "window_too_large"


async def test_extreme_date_window_never_500s(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # ``date.min``..``date.max`` must never crash the endpoint (no ``OverflowError`` -> 500); the
    # window cap rejects it cleanly with a 422 long before the service pads the range.
    resp = await wired_client.get(
        SLOTS,
        params={
            "event_type": str(uuid.uuid4()),
            "tz": "UTC",
            "from": date.min.isoformat(),
            "to": date.max.isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "window_too_large"


async def test_happy_path_returns_utc_slots_and_echoes_tz(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    resp = await wired_client.get(
        SLOTS,
        params={"event_type": seeded["event_type_id"], "tz": "America/New_York", **_window()},
        headers=seeded["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_type_id"] == seeded["event_type_id"]
    assert body["timezone"] == "America/New_York"  # the requested display tz is echoed back
    assert body["availability"] == "ok"  # no external calendar connected
    assert len(body["slots"]) > 0
    # Every slot bound is an absolute UTC instant regardless of the requested display tz.
    first = body["slots"][0]
    assert first["start"].endswith("+00:00") or first["start"].endswith("Z")
    assert datetime.fromisoformat(first["start"]).utcoffset() == timedelta(0)
