"""The workflow + template HTTP contract (RF-24), end to end through the real app over PostgreSQL.

``db``-marked (whole module): they need a real server (``AETHERCAL_TEST_DATABASE_URL``), skip in the
offline matrix, and run in CI's ``test-db`` job. They are the executable spec for the endpoints —
create → read → list → patch → activate/deactivate, plus auth, tenant isolation and the error
mapping.

The refusals matter as much as the happy path. Each 422 below is a rule that, had the API accepted
it, would produce a workflow that raises nothing and quietly sends the wrong thing — or nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.api import workflows
from aethercal.server.db.models import Schedule, Tenant, User
from aethercal.server.services.api_keys import issue_api_key

pytestmark = pytest.mark.db

WORKFLOWS = "/workflows/"
TEMPLATES = "/workflow-templates/"


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(workflows.router)
    app.include_router(workflows.templates_router)
    return client


@pytest_asyncio.fixture
async def seeded(app: FastAPI) -> dict[str, Any]:
    """A tenant with a host, a schedule and an API key."""
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
    return {"headers": {"Authorization": f"Bearer {full_key}"}}


def _reminder(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "24h reminder",
        "trigger": "before_start",
        "offset_minutes": -1440,
        "steps": [{"channel": "email", "kind": "reminder", "position": 0}],
    }
    payload.update(overrides)
    return payload


async def test_list_requires_auth(wired_client: AsyncClient) -> None:
    assert (await wired_client.get(WORKFLOWS)).status_code == 401
    assert (await wired_client.get(TEMPLATES)).status_code == 401


async def test_unknown_id_returns_404(
    wired_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await wired_client.get(f"/workflows/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


async def test_full_lifecycle(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    headers = seeded["headers"]

    created = await wired_client.post(WORKFLOWS, json=_reminder(), headers=headers)
    assert created.status_code == 201
    body = created.json()
    workflow_id = body["id"]
    assert body["active"] is True
    assert body["offset_minutes"] == -1440
    assert [(step["channel"], step["kind"]) for step in body["steps"]] == [("email", "reminder")]

    got = await wired_client.get(f"/workflows/{workflow_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == workflow_id

    listed = await wired_client.get(WORKFLOWS, headers=headers)
    assert listed.status_code == 200
    assert workflow_id in {row["id"] for row in listed.json()}

    patched = await wired_client.patch(
        f"/workflows/{workflow_id}", json={"offset_minutes": -120}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["offset_minutes"] == -120
    # ``updated_at`` is computed by the DATABASE (``onupdate=func.now()``), so the UPDATE leaves the
    # attribute expired and its new value exists only in Postgres. Asserting that it MOVED — rather
    # than merely that the response parsed — is what pins the reload: read it lazily instead and the
    # handler crashes with MissingGreenlet.
    assert patched.json()["updated_at"] >= body["updated_at"]

    off = await wired_client.post(f"/workflows/{workflow_id}/deactivate", headers=headers)
    assert off.status_code == 200
    assert off.json()["active"] is False

    on = await wired_client.post(f"/workflows/{workflow_id}/activate", headers=headers)
    assert on.status_code == 200
    assert on.json()["active"] is True

    # DELETE is a soft delete (deactivate), exactly like event_types.
    deleted = await wired_client.delete(f"/workflows/{workflow_id}", headers=headers)
    assert deleted.status_code == 204
    after = await wired_client.get(f"/workflows/{workflow_id}", headers=headers)
    assert after.status_code == 200
    assert after.json()["active"] is False


async def test_duplicate_name_conflicts(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    headers = seeded["headers"]
    assert (
        await wired_client.post(WORKFLOWS, json=_reminder(), headers=headers)
    ).status_code == 201
    second = await wired_client.post(WORKFLOWS, json=_reminder(), headers=headers)
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "duplicate_name"


async def test_an_offset_on_an_event_shaped_trigger_is_rejected(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    """The engine ignores the offset for ``on_cancel``, so storing one would schedule a message that
    in fact goes out immediately. Rejected at the edge (FastAPI 422)."""
    resp = await wired_client.post(
        WORKFLOWS,
        json=_reminder(
            name="late",
            trigger="on_cancel",
            offset_minutes=120,
            steps=[{"channel": "email", "kind": "cancellation", "position": 0}],
        ),
        headers=seeded["headers"],
    )
    assert resp.status_code == 422


async def test_a_rule_with_no_steps_is_rejected(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    resp = await wired_client.post(WORKFLOWS, json=_reminder(steps=[]), headers=seeded["headers"])
    assert resp.status_code == 422


async def test_a_step_with_no_template_is_rejected_by_the_service(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    """A WhatsApp step with no body would be skipped at every send, silently, for ever."""
    resp = await wired_client.post(
        WORKFLOWS,
        json=_reminder(steps=[{"channel": "whatsapp", "kind": "reminder", "position": 0}]),
        headers=seeded["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_rule"


async def test_a_bad_event_type_reference_is_unprocessable(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    resp = await wired_client.post(
        WORKFLOWS,
        json=_reminder(event_type_id=str(uuid.uuid4())),
        headers=seeded["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_reference"


async def test_patching_only_the_trigger_is_validated_against_the_merged_rule(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    """``{"trigger": "on_cancel"}`` is a self-consistent payload that would leave the stored
    ``-1440`` behind — ignored by the engine for ever. The service checks the RESULT."""
    headers = seeded["headers"]
    created = await wired_client.post(WORKFLOWS, json=_reminder(), headers=headers)
    workflow_id = created.json()["id"]

    resp = await wired_client.patch(
        f"/workflows/{workflow_id}", json={"trigger": "on_cancel"}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_rule"


async def test_another_tenants_workflow_is_a_404(
    wired_client: AsyncClient, seeded: dict[str, Any], auth_headers: dict[str, str]
) -> None:
    created = await wired_client.post(WORKFLOWS, json=_reminder(), headers=seeded["headers"])
    workflow_id = created.json()["id"]

    # ``auth_headers`` belongs to a DIFFERENT tenant.
    assert (
        await wired_client.get(f"/workflows/{workflow_id}", headers=auth_headers)
    ).status_code == 404
    assert (
        await wired_client.patch(
            f"/workflows/{workflow_id}", json={"active": False}, headers=auth_headers
        )
    ).status_code == 404
    assert (
        await wired_client.post(f"/workflows/{workflow_id}/deactivate", headers=auth_headers)
    ).status_code == 404


# --------------------------------------------------------------------------------------
# Templates.
# --------------------------------------------------------------------------------------


async def test_template_lifecycle(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    headers = seeded["headers"]
    created = await wired_client.post(
        TEMPLATES,
        json={
            "channel": "whatsapp",
            "kind": "reminder",
            "locale": "es",
            "body": "Hola {{guest_name}}, te esperamos a las {{start_local}}",
        },
        headers=headers,
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    assert created.json()["subject"] is None

    listed = await wired_client.get(TEMPLATES, headers=headers)
    assert template_id in {row["id"] for row in listed.json()}

    patched = await wired_client.patch(
        f"/workflow-templates/{template_id}", json={"body": "Hola {{guest_name}}"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "Hola {{guest_name}}"

    # With a template in place, the WhatsApp step becomes authorable.
    rule = await wired_client.post(
        WORKFLOWS,
        json=_reminder(steps=[{"channel": "whatsapp", "kind": "reminder", "position": 0}]),
        headers=headers,
    )
    assert rule.status_code == 201

    # …and the template it depends on can no longer be deleted out from under it.
    refused = await wired_client.delete(f"/workflow-templates/{template_id}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "template_in_use"


async def test_a_template_may_not_carry_an_unknown_variable(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    """The body is data, never instructions: an unknown ``{{...}}`` would render as garbage into a
    real guest's message."""
    resp = await wired_client.post(
        TEMPLATES,
        json={
            "channel": "sms",
            "kind": "reminder",
            "locale": "es",
            "body": "Hola {{guest_secret}}",
        },
        headers=seeded["headers"],
    )
    assert resp.status_code == 422


async def test_a_duplicate_template_identity_conflicts(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    headers = seeded["headers"]
    payload = {"channel": "sms", "kind": "reminder", "locale": "es", "body": "Hola"}
    assert (await wired_client.post(TEMPLATES, json=payload, headers=headers)).status_code == 201
    second = await wired_client.post(TEMPLATES, json=payload, headers=headers)
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "duplicate_template"


async def test_another_tenants_template_is_a_404(
    wired_client: AsyncClient, seeded: dict[str, Any], auth_headers: dict[str, str]
) -> None:
    created = await wired_client.post(
        TEMPLATES,
        json={"channel": "sms", "kind": "reminder", "locale": "es", "body": "Hola"},
        headers=seeded["headers"],
    )
    template_id = created.json()["id"]

    assert (
        await wired_client.get(f"/workflow-templates/{template_id}", headers=auth_headers)
    ).status_code == 404
    assert (
        await wired_client.delete(f"/workflow-templates/{template_id}", headers=auth_headers)
    ).status_code == 404
