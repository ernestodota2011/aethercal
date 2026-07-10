"""End-to-end webhook CRUD through the real app over PostgreSQL (RF-17).

These are ``db``-marked: they need a real server (``AETHERCAL_TEST_DATABASE_URL``) and skip in the
offline matrix, running in CI's ``test-db`` job. They are the executable spec for the subscription
API — the secret is returned only on create, reads never leak it, and every route is tenant-scoped.
The router is mounted onto the app at fixture time (an integrator wires it into ``api_router`` in
F1-08), so paths here are ``/webhooks`` (no ``/api/v1`` prefix).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.api import webhooks
from aethercal.server.services.api_keys import issue_api_key

COLLECTION = "/webhooks"


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    """The app with the webhooks router mounted (F1-08 wires it into api_router for real)."""
    app.include_router(webhooks.router)
    return client


@pytest_asyncio.fixture
async def other_auth_headers(app: FastAPI, tenant_factory) -> dict[str, str]:
    """Bearer header for a SECOND tenant, to prove cross-tenant isolation."""
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        created = await tenant_factory(session)
        _, full_key = await issue_api_key(session, tenant_id=created.id, name="other-key")
    return {"Authorization": f"Bearer {full_key}"}


@pytest.mark.db
async def test_create_requires_auth(wired_client: AsyncClient) -> None:
    resp = await wired_client.post(
        COLLECTION, json={"url": "https://consumer.test/hook", "events": ["booking.created"]}
    )
    assert resp.status_code == 401


@pytest.mark.db
async def test_create_returns_secret_once_and_reads_never_do(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.post(
        COLLECTION,
        headers=auth_headers,
        json={"url": "https://consumer.test/hook", "events": ["booking.created"]},
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["secret"]  # returned exactly once
    assert created["events"] == ["booking.created"]
    assert created["active"] is True
    webhook_id = created["id"]

    # The secret never appears on any subsequent read.
    got = await wired_client.get(f"{COLLECTION}/{webhook_id}", headers=auth_headers)
    assert got.status_code == 200
    assert "secret" not in got.json()

    listed = await wired_client.get(COLLECTION, headers=auth_headers)
    assert listed.status_code == 200
    assert [w["id"] for w in listed.json()] == [webhook_id]
    assert all("secret" not in w for w in listed.json())


@pytest.mark.db
async def test_create_honors_a_supplied_secret(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.post(
        COLLECTION,
        headers=auth_headers,
        json={
            "url": "https://consumer.test/hook",
            "events": ["booking.created"],
            "secret": "caller-chosen-secret",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["secret"] == "caller-chosen-secret"


@pytest.mark.db
async def test_create_rejects_an_unknown_event(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.post(
        COLLECTION,
        headers=auth_headers,
        json={"url": "https://consumer.test/hook", "events": ["booking.exploded"]},
    )
    assert resp.status_code == 422


@pytest.mark.db
async def test_create_rejects_a_non_http_scheme(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # The scheme validator fast-fails a non-http(s) URL at registration (RF-17 / RNF-5); the
    # authoritative private-IP block still happens send-time in the delivery worker.
    resp = await wired_client.post(
        COLLECTION,
        headers=auth_headers,
        json={"url": "ftp://consumer.test/hook", "events": ["booking.created"]},
    )
    assert resp.status_code == 422


@pytest.mark.db
async def test_get_unknown_is_404(wired_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    resp = await wired_client.get(f"{COLLECTION}/{missing}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.db
async def test_patch_toggles_active_and_events(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await wired_client.post(
            COLLECTION,
            headers=auth_headers,
            json={"url": "https://consumer.test/hook", "events": ["booking.created"]},
        )
    ).json()
    webhook_id = created["id"]

    resp = await wired_client.patch(
        f"{COLLECTION}/{webhook_id}",
        headers=auth_headers,
        json={"active": False, "events": ["booking.cancelled", "booking.rescheduled"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is False
    assert body["events"] == ["booking.cancelled", "booking.rescheduled"]
    assert body["url"] == "https://consumer.test/hook"  # untouched


@pytest.mark.db
async def test_delete_then_get_is_404(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await wired_client.post(
            COLLECTION,
            headers=auth_headers,
            json={"url": "https://consumer.test/hook", "events": ["booking.created"]},
        )
    ).json()
    webhook_id = created["id"]

    deleted = await wired_client.delete(f"{COLLECTION}/{webhook_id}", headers=auth_headers)
    assert deleted.status_code == 204

    gone = await wired_client.get(f"{COLLECTION}/{webhook_id}", headers=auth_headers)
    assert gone.status_code == 404


@pytest.mark.db
async def test_another_tenant_cannot_see_or_touch_the_webhook(
    wired_client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    created = (
        await wired_client.post(
            COLLECTION,
            headers=auth_headers,
            json={"url": "https://consumer.test/hook", "events": ["booking.created"]},
        )
    ).json()
    webhook_id = created["id"]

    # The other tenant sees an empty list and cannot read/patch/delete it.
    assert (await wired_client.get(COLLECTION, headers=other_auth_headers)).json() == []
    assert (
        await wired_client.get(f"{COLLECTION}/{webhook_id}", headers=other_auth_headers)
    ).status_code == 404
    assert (
        await wired_client.patch(
            f"{COLLECTION}/{webhook_id}", headers=other_auth_headers, json={"active": False}
        )
    ).status_code == 404
    assert (
        await wired_client.delete(f"{COLLECTION}/{webhook_id}", headers=other_auth_headers)
    ).status_code == 404
