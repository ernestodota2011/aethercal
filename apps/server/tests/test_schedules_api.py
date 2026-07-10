"""API tests for the Schedules + Date Overrides router (RF-15).

Two layers:

* An offline structural test that asserts the router's prefix and its exact (method, path) surface —
  it runs everywhere (no database) and locks the contract F1-04 and the admin UI build against.
* ``db``-marked end-to-end tests through the real ASGI app over PostgreSQL. They skip in the offline
  matrix (no ``AETHERCAL_TEST_DATABASE_URL``) and run in CI's ``test-db`` job; they are the
  executable spec for the HTTP behavior (status codes, tenant scoping, error mapping).

The ``wired_client`` fixture mounts this feature router on the shared ``app`` harness (whose
protected probe already proves auth), so these tests exercise the real ``require_api_key`` +
``get_session`` stack end-to-end.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

from aethercal.server.api import schedules

_HTTP_VERBS = {"GET", "POST", "PATCH", "PUT", "DELETE"}

EXPECTED_ROUTES = {
    ("POST", "/schedules/"),
    ("GET", "/schedules/"),
    ("GET", "/schedules/{schedule_id}"),
    ("PATCH", "/schedules/{schedule_id}"),
    ("DELETE", "/schedules/{schedule_id}"),
    ("POST", "/schedules/{schedule_id}/date-overrides"),
    ("GET", "/schedules/{schedule_id}/date-overrides"),
    ("DELETE", "/schedules/date-overrides/{override_id}"),
}


def test_router_exposes_the_expected_surface() -> None:
    assert schedules.router.prefix == "/schedules"
    actual = {
        (method, route.path)
        for route in schedules.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in _HTTP_VERBS
    }
    assert actual == EXPECTED_ROUTES


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(schedules.router)
    return client


def _schedule_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Main",
        "timezone": "America/New_York",
        "rules": {"0": [{"start": "09:00", "end": "17:00"}]},
    }
    body.update(overrides)
    return body


@pytest.mark.db
async def test_create_requires_auth(wired_client: AsyncClient) -> None:
    resp = await wired_client.post("/schedules/", json=_schedule_body())
    assert resp.status_code == 401


@pytest.mark.db
async def test_create_and_get_schedule(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Main"
    assert body["timezone"] == "America/New_York"
    assert body["rules"] == {"0": [{"start": "09:00", "end": "17:00"}]}
    schedule_id = body["id"]

    fetched = await wired_client.get(f"/schedules/{schedule_id}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == schedule_id


@pytest.mark.db
async def test_list_schedules(wired_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    await wired_client.post("/schedules/", json=_schedule_body(name="A"), headers=auth_headers)
    await wired_client.post("/schedules/", json=_schedule_body(name="B"), headers=auth_headers)
    resp = await wired_client.get("/schedules/", headers=auth_headers)
    assert resp.status_code == 200
    assert [s["name"] for s in resp.json()] == ["A", "B"]


@pytest.mark.db
async def test_duplicate_name_conflicts(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    dup = await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    assert dup.status_code == 409


@pytest.mark.db
async def test_invalid_timezone_is_unprocessable(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.post(
        "/schedules/", json=_schedule_body(timezone="Mars/Phobos"), headers=auth_headers
    )
    assert resp.status_code == 422


@pytest.mark.db
async def test_overlapping_ranges_is_unprocessable(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = _schedule_body(
        rules={"0": [{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]}
    )
    resp = await wired_client.post("/schedules/", json=body, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.db
async def test_get_unknown_schedule_is_not_found(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.get(
        "/schedules/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.db
async def test_patch_schedule(wired_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    schedule_id = created.json()["id"]
    patched = await wired_client.patch(
        f"/schedules/{schedule_id}", json={"name": "Renamed"}, headers=auth_headers
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"


@pytest.mark.db
async def test_delete_schedule(wired_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    schedule_id = created.json()["id"]
    deleted = await wired_client.delete(f"/schedules/{schedule_id}", headers=auth_headers)
    assert deleted.status_code == 204
    gone = await wired_client.get(f"/schedules/{schedule_id}", headers=auth_headers)
    assert gone.status_code == 404


@pytest.mark.db
async def test_date_override_lifecycle(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await wired_client.post("/schedules/", json=_schedule_body(), headers=auth_headers)
    schedule_id = created.json()["id"]

    # A closed holiday (empty ranges).
    added = await wired_client.post(
        f"/schedules/{schedule_id}/date-overrides",
        json={"date": "2026-12-25", "ranges": []},
        headers=auth_headers,
    )
    assert added.status_code == 201
    override_id = added.json()["id"]
    assert added.json()["ranges"] == []

    # Second override for the same date conflicts.
    dup = await wired_client.post(
        f"/schedules/{schedule_id}/date-overrides",
        json={"date": "2026-12-25", "ranges": []},
        headers=auth_headers,
    )
    assert dup.status_code == 409

    listed = await wired_client.get(
        f"/schedules/{schedule_id}/date-overrides", headers=auth_headers
    )
    assert listed.status_code == 200
    assert [o["id"] for o in listed.json()] == [override_id]

    removed = await wired_client.delete(
        f"/schedules/date-overrides/{override_id}", headers=auth_headers
    )
    assert removed.status_code == 204
    after = await wired_client.get(f"/schedules/{schedule_id}/date-overrides", headers=auth_headers)
    assert after.json() == []
