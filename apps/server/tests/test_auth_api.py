"""End-to-end API-key auth through the real app over PostgreSQL (F1-17).

These are ``db``-marked: they need a real server (``AETHERCAL_TEST_DATABASE_URL``) and skip in the
offline matrix, running in CI's ``test-db`` job. They prove the ``require_api_key`` dependency
returns 401/200 through the full ASGI stack — the executable spec for the auth contract.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

PROTECTED = "/api/v1/_probe/whoami"


@pytest.mark.db
async def test_health_is_open_without_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.db
async def test_protected_route_rejects_missing_key(client: AsyncClient) -> None:
    resp = await client.get(PROTECTED)
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized", "message": "Invalid or missing API key"}
    assert resp.headers.get("www-authenticate") == "Bearer"


@pytest.mark.db
async def test_protected_route_rejects_unknown_key(client: AsyncClient) -> None:
    resp = await client.get(
        PROTECTED,
        headers={"Authorization": "Bearer ack_ZZZZZZZZ_this-is-a-well-formed-but-unknown-secret"},
    )
    assert resp.status_code == 401
    # Same generic body as the missing-key case (RF-16: no leak on invalid key).
    assert resp.json() == {"error": "unauthorized", "message": "Invalid or missing API key"}


@pytest.mark.db
async def test_protected_route_accepts_valid_key(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.get(PROTECTED, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "tenant_id" in body
    assert "api_key_id" in body
