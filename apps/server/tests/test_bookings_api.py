"""End-to-end booking endpoints through the real app over PostgreSQL (F1-05, RF-07/RF-09/RF-16).

``db``-marked (whole module): they need ``AETHERCAL_TEST_DATABASE_URL``, skip in the offline
matrix, and run in CI's ``test-db`` job. They are the executable HTTP spec for the booking flow:
auth, the create happy path (201), the duplicate-slot conflict (409), guest-token-gated cancel
(RF-09), and reschedule. ``wired_client`` mounts the feature router directly (the orchestrator wires
it under ``/api/v1`` in production; here the path is ``/bookings/``); ``seeded`` provisions a tenant
with a host, an always-open UTC schedule, a 30-minute event type, its API key, and two genuinely
offered slots (fetched from the slots engine so they are valid against the real clock).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.api import bookings
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Schedule,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.event_types import deactivate_event_type
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    issue_guest_token,
)
from aethercal.server.services.outbox import OutboxEffect, email_dedupe_key
from aethercal.server.services.slots import compute_slots

pytestmark = pytest.mark.db

BOOKINGS = "/bookings/"
_APP_SECRET = "test-app-secret"
# Open every weekday, all day, in UTC → a near-future slot is always on offer.
_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(bookings.router)
    return client


@pytest_asyncio.fixture
async def seeded(app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Seed a tenant + host + open schedule + event type + API key; return ids, headers, slots.

    ==On the OWNER engine.== Under ``FORCE ROW LEVEL SECURITY`` these INSERTs carry a business
    that nothing has bound yet, so the ``WITH CHECK`` refuses every one of them on the app role.
    The REQUEST is the system under test, and it binds its own business — from the API key this
    fixture returns, through the resolver, in ``require_api_key``.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Seeded Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_ALWAYS_OPEN)
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

        now = datetime.now(UTC)
        tomorrow = (now + timedelta(days=1)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=tomorrow,
            window_to=tomorrow,
            now=now,
        )
        assert result is not None and len(result.slots) >= 2
        tenant_id, event_type_id = tenant.id, event_type.id
        slot1 = result.slots[0].start
        slot2 = result.slots[1].start
    return {
        "headers": {"Authorization": f"Bearer {full_key}"},
        "tenant_id": tenant_id,
        "event_type_id": str(event_type_id),
        "slot1": slot1.isoformat(),
        "slot2": slot2.isoformat(),
    }


def _payload(seeded: dict[str, Any], start: str) -> dict[str, Any]:
    return {
        "event_type_id": seeded["event_type_id"],
        "start": start,
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "UTC",
    }


async def _mint_token(
    owner_maker: async_sessionmaker[AsyncSession],
    *,
    booking_id: uuid.UUID,
    tenant_id: uuid.UUID,
    purpose: GuestTokenPurpose,
) -> str:
    """Arrangement: the guest link the product would have e-mailed. On the OWNER engine."""
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session, session.begin():
        return await issue_guest_token(
            session,
            GuestTokenSigner(_APP_SECRET),
            booking_id=booking_id,
            tenant_id=tenant_id,
            purpose=purpose,
            ttl=timedelta(days=1),
        )


async def test_requires_auth(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    resp = await wired_client.post(BOOKINGS, json=_payload(seeded, seeded["slot1"]))
    assert resp.status_code == 401


async def test_create_returns_201_and_confirms(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    resp = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["event_type_id"] == seeded["event_type_id"]
    assert "external_event_id" not in body  # internal field never leaks (RF-16)


async def test_http_create_enqueues_email_intent_but_not_google(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    """R6 deferral is clean + safe: the real HTTP booking path ALWAYS enqueues the confirmation
    email intent (durable, drained post-commit), but with no host ``ExternalConnection`` resolved it
    enqueues NO Google intent — Google stays cleanly deferred (the outbox never queues a Google
    effect in a half-wired state). Wiring the host connection is the documented remaining step (live
    OAuth, out of this scope)."""
    resp = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    assert resp.status_code == 201
    booking_id = uuid.UUID(resp.json()["id"])

    # Observation, on the OWNER engine: what the REQUEST wrote, read back by something that can
    # see every business — so a row filed under the wrong one would show up, not vanish.
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session:
        rows = list(
            (await session.scalars(select(Outbox).where(Outbox.booking_id == booking_id))).all()
        )
    keys = {(r.effect, r.dedupe_key) for r in rows}
    assert (OutboxEffect.EMAIL.value, email_dedupe_key(NotificationKind.CONFIRMATION)) in keys
    assert all(r.effect != OutboxEffect.GOOGLE.value for r in rows)  # Google deferred → not queued


async def test_duplicate_slot_returns_409(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    first = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    assert first.status_code == 201
    second = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "slot_unavailable"


async def test_bad_payload_returns_422(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    bad = {**_payload(seeded, seeded["slot1"]), "guest_email": "not-an-email"}
    resp = await wired_client.post(BOOKINGS, json=bad, headers=seeded["headers"])
    assert resp.status_code == 422


async def test_get_and_list(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    created = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    booking_id = created.json()["id"]

    one = await wired_client.get(f"{BOOKINGS}{booking_id}", headers=seeded["headers"])
    assert one.status_code == 200 and one.json()["id"] == booking_id

    listed = await wired_client.get(BOOKINGS, headers=seeded["headers"])
    assert listed.status_code == 200
    assert booking_id in [b["id"] for b in listed.json()]

    missing = await wired_client.get(f"{BOOKINGS}{uuid.uuid4()}", headers=seeded["headers"])
    assert missing.status_code == 404


async def test_cancel_via_guest_token(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    created = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    booking_id = created.json()["id"]
    token = await _mint_token(
        owner_maker,
        booking_id=uuid.UUID(booking_id),
        tenant_id=seeded["tenant_id"],
        purpose=GuestTokenPurpose.CANCEL,
    )

    # A guest link (no API key) cancels the booking.
    ok = await wired_client.post(f"{BOOKINGS}{booking_id}/cancel", params={"token": token})
    assert ok.status_code == 200
    assert ok.json()["status"] == "cancelled"

    # A bogus token is refused generically (RF-09: no booking data leaked).
    bad = await wired_client.post(f"{BOOKINGS}{booking_id}/cancel", params={"token": "garbage"})
    assert bad.status_code == 403


async def test_reschedule_via_api_key(wired_client: AsyncClient, seeded: dict[str, Any]) -> None:
    created = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    original_id = created.json()["id"]

    moved = await wired_client.post(
        f"{BOOKINGS}{original_id}/reschedule",
        json={"new_start": seeded["slot2"]},
        headers=seeded["headers"],
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["id"] != original_id
    assert body["status"] == "confirmed"
    assert body["rescheduled_from_id"] == original_id

    # The original is now cancelled, freeing its slot.
    old = await wired_client.get(f"{BOOKINGS}{original_id}", headers=seeded["headers"])
    assert old.json()["status"] == "cancelled"


# --------------------------------------------------------------------------------------
# No-show (RF-25) over HTTP. The HOST marks it — never the guest.
# --------------------------------------------------------------------------------------


async def _book_in_the_past(
    owner_maker: async_sessionmaker[AsyncSession], seeded: dict[str, Any]
) -> uuid.UUID:
    """A CONFIRMED booking whose appointment has already ENDED.

    Inserted directly: the create path (rightly) refuses to book a slot in the past, and a no-show
    is only ever a statement about an appointment that already happened. On the OWNER engine.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    start = datetime.now(UTC) - timedelta(hours=2)
    async with sessionmaker() as session, session.begin():
        booking = Booking(
            tenant_id=seeded["tenant_id"],
            event_type_id=uuid.UUID(seeded["event_type_id"]),
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=BookingStatus.CONFIRMED,
            # A CONFIRMED booking carries the instant it became so — leaving it NULL would make the
            # outbound belt (B-05a) treat this real, finished appointment as an unpaid hold and
            # SILENCE its no-show webhook. It was confirmed when it was booked, before it ended.
            confirmed_at=start - timedelta(hours=1),
            guest_name="Ada Lovelace",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        return booking.id


async def test_no_show_requires_the_api_key(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    """No guest-token door, deliberately: cancelling is the guest's right, but declaring that they
    failed to show up is the HOST's judgement about them. A guest-reachable no-show would also hand
    anyone holding an emailed link a way to smear the record."""
    booking_id = await _book_in_the_past(owner_maker, seeded)

    resp = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show")

    assert resp.status_code == 401


async def test_no_show_marks_a_finished_booking(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    booking_id = await _book_in_the_past(owner_maker, seeded)

    resp = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    assert resp.status_code == 200
    assert resp.json()["status"] == "no_show"


async def test_no_show_is_idempotent_over_http(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    booking_id = await _book_in_the_past(owner_maker, seeded)
    first = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    again = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    assert first.status_code == 200
    assert again.status_code == 200
    assert again.json()["status"] == "no_show"


async def test_no_show_of_an_unfinished_booking_returns_409_not_ended(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    """A DISTINCT machine code. Collapsing "it has not happened yet" into the generic conflict is
    how an admin ends up staring at "Booking cannot be rescheduled" after clicking *no-show*."""
    created = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    booking_id = created.json()["id"]

    resp = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "not_ended"


async def test_no_show_of_a_cancelled_booking_returns_409_not_active(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    booking_id = await _book_in_the_past(owner_maker, seeded)
    await wired_client.post(f"{BOOKINGS}{booking_id}/cancel", headers=seeded["headers"])

    resp = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "not_active"


async def test_no_show_of_an_unknown_booking_returns_404(
    wired_client: AsyncClient, seeded: dict[str, Any]
) -> None:
    resp = await wired_client.post(f"{BOOKINGS}{uuid.uuid4()}/no-show", headers=seeded["headers"])

    assert resp.status_code == 404


async def test_no_show_fans_out_the_webhook_over_http(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    """End to end on the real database: the transition and its delivery row commit together."""
    booking_id = await _book_in_the_past(owner_maker, seeded)
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session, session.begin():
        session.add(
            Webhook(
                tenant_id=seeded["tenant_id"],
                url="https://consumer.test/hook",
                secret=b"opaque",
                events=["booking.no_show"],
                active=True,
            )
        )

    resp = await wired_client.post(f"{BOOKINGS}{booking_id}/no-show", headers=seeded["headers"])

    assert resp.status_code == 200
    async with sessionmaker() as session:
        queued = (
            await session.scalars(
                select(WebhookDelivery).where(WebhookDelivery.event == "booking.no_show")
            )
        ).all()
    assert len(queued) == 1
    assert queued[0].payload["data"]["id"] == str(booking_id)


async def test_a_deactivated_event_type_is_indistinguishable_from_an_unknown_one(
    owner_maker: async_sessionmaker[AsyncSession],
    wired_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    """RF-14 over HTTP: "deleting" an event type must actually stop it taking bookings — and must
    not become an oracle for which ones a business has switched off.

    Deactivation (``active = False``, what ``DELETE /event-types/{id}`` performs) used to change
    nothing a guest could observe: ``POST /bookings`` went on confirming appointments for the
    withdrawn service, because no booking path ever read the flag.

    Both halves are asserted, and the second matters as much as the first: the deactivated type must
    answer with the EXACT status, code and message an id that never existed answers with. A distinct
    "this one is disabled" reply would let a stranger enumerate a business's retired services.
    """
    unknown = await wired_client.post(
        BOOKINGS,
        json={**_payload(seeded, seeded["slot1"]), "event_type_id": str(uuid.uuid4())},
        headers=seeded["headers"],
    )
    assert unknown.status_code == 404

    # Soft-delete through the service (the event-types router is not mounted on this client).
    # Arrangement for the request that follows — so, the OWNER engine.
    sessionmaker: async_sessionmaker[AsyncSession] = owner_maker
    async with sessionmaker() as session, session.begin():
        assert await deactivate_event_type(
            session,
            tenant_id=seeded["tenant_id"],
            event_type_id=uuid.UUID(seeded["event_type_id"]),
        )

    deactivated = await wired_client.post(
        BOOKINGS, json=_payload(seeded, seeded["slot1"]), headers=seeded["headers"]
    )
    assert deactivated.status_code == 404
    assert deactivated.json()["detail"] == unknown.json()["detail"]  # byte-for-byte, no oracle

    # (That it also publishes no SLOTS is proven offline, in
    # ``test_slots_service.test_a_deactivated_event_type_publishes_no_slots``: the slots router is
    # not mounted on this client, so asserting it here would pass on a ROUTING 404 and prove
    # nothing at all.)
