"""End-to-end EventType CRUD through the real app over PostgreSQL (RF-14).

These are ``db``-marked (whole module): they need a real server (``AETHERCAL_TEST_DATABASE_URL``);
they skip in the offline matrix and run in CI's ``test-db`` job. They are the executable spec for
the event-types HTTP contract — create → read → list → patch → delete, plus auth and error mapping.

``wired_client`` mounts the feature router directly on the app (the orchestrator wires it under
``/api/v1`` in production; here the path is ``/event-types``). ``auth_headers`` covers the no-refs
cases; a real create needs a host + schedule owned by the tenant, so ``seeded`` provisions a tenant
with both plus its API key.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.api import event_types
from aethercal.server.db.models import Schedule, Tenant, User
from aethercal.server.services.api_keys import issue_api_key

pytestmark = pytest.mark.db

COLLECTION = "/event-types/"


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(event_types.router)
    return client


@pytest_asyncio.fixture
async def seeded(app: FastAPI) -> dict[str, Any]:
    """Provision a tenant with a host user, a schedule, and an API key; return headers + ref ids."""
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Seeded Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Default", timezone="UTC", rules={})
        session.add_all([host, schedule])
        await session.flush()
        _, full_key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")
        host_id, schedule_id = str(host.id), str(schedule.id)
    return {
        "headers": {"Authorization": f"Bearer {full_key}"},
        "host_id": host_id,
        "schedule_id": schedule_id,
    }


def _create_payload(seeded: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "host_id": seeded["host_id"],
        "schedule_id": seeded["schedule_id"],
        "slug": "intro-call",
        "title": "Intro Call",
        "duration_seconds": 1800,
        "max_advance_seconds": 60 * 60 * 24 * 30,
    }
    payload.update(overrides)
    return payload


async def test_list_requires_auth(wired_client: AsyncClient) -> None:
    resp = await wired_client.get(COLLECTION)
    assert resp.status_code == 401


async def test_unknown_id_returns_404(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.get(f"/event-types/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


async def test_full_lifecycle(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    headers = seeded["headers"]

    # create → 201
    created = await wired_client.post(COLLECTION, json=_create_payload(seeded), headers=headers)
    assert created.status_code == 201
    body = created.json()
    event_id = body["id"]
    assert body["slug"] == "intro-call"
    assert body["active"] is True
    assert body["duration_seconds"] == 1800

    # read → 200
    got = await wired_client.get(f"/event-types/{event_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == event_id

    # list → 200 and contains it
    listed = await wired_client.get(COLLECTION, headers=headers)
    assert listed.status_code == 200
    assert event_id in {row["id"] for row in listed.json()}

    # patch → 200 and applies the change
    patched = await wired_client.patch(
        f"/event-types/{event_id}", json={"title": "Renamed"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"

    # delete → 204 (soft), then the row is still readable but inactive
    deleted = await wired_client.delete(f"/event-types/{event_id}", headers=headers)
    assert deleted.status_code == 204
    after = await wired_client.get(f"/event-types/{event_id}", headers=headers)
    assert after.status_code == 200
    assert after.json()["active"] is False


async def test_duplicate_slug_conflicts(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    headers = seeded["headers"]
    first = await wired_client.post(
        COLLECTION, json=_create_payload(seeded, slug="dup"), headers=headers
    )
    assert first.status_code == 201

    second = await wired_client.post(
        COLLECTION, json=_create_payload(seeded, slug="dup"), headers=headers
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "duplicate_slug"


async def test_bad_reference_is_unprocessable(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    headers = seeded["headers"]
    resp = await wired_client.post(
        COLLECTION,
        json=_create_payload(seeded, schedule_id=str(uuid.uuid4())),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_reference"


async def test_create_rejects_out_of_bounds_payload(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    # duration_seconds must be > 0 — the schema rejects it at the edge (FastAPI 422).
    resp = await wired_client.post(
        COLLECTION, json=_create_payload(seeded, duration_seconds=0), headers=seeded["headers"]
    )
    assert resp.status_code == 422
