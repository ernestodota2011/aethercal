"""The guest's phone consent, end to end over real PostgreSQL (RF-24, RNF-8).

Every other test of this feature proves one link. This proves the CHAIN, because the chain is where
the silent no-op lives: the booking page renders a checkbox, the guest ticks it, and the question
that actually matters — *did the column get written, and does the WhatsApp step read it?* — is only
answerable at the far end, in Postgres, after a real drain.

Until this batch the consent gate (``services/outbox._require_phone_consent``) could only be tested
by setting ``booking.guest_phone_consent_at`` **by hand**, because no write path existed. The
columns landed in migration ``0005`` and nothing on the public booking flow could reach them:
``BookingCreate`` had no phone field, ``BookingParams`` had none, and ``create_booking`` never wrote
them. A checkbox added to the form alone would have submitted, validated, confirmed the booking,
raised nothing — and left the column NULL for ever.

So these tests drive the REAL surfaces the booking page talks to — ``GET /event-types/`` and
``POST /bookings/`` — and then assert the EFFECTIVE state:

* the column, re-read from Postgres (tz-aware, since ``DateTime(timezone=True)`` is the real type;
  only SQLite hands it back naive);
* the outbox row after a real drain, with a CONFIGURED WhatsApp sender — so "nobody could have sent
  it" is never the reason a test passes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.api import bookings, event_types
from aethercal.server.channels import Channel
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Schedule,
    Tenant,
    User,
    Workflow,
    WorkflowStep,
    WorkflowTemplate,
)
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.integrations.messaging.guard import DailyCaps
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.services.outbox import (
    OutboxEffect,
    drain_outbox,
    make_booking_effect_executor,
)
from aethercal.server.services.slots import compute_slots

pytestmark = pytest.mark.db

BOOKINGS = "/bookings/"
EVENT_TYPES = "/event-types/"
_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}

Sessionmaker = async_sessionmaker[AsyncSession]


class _RecordingEmailSender:
    """A configured e-mail sender — the booking's confirmation is not what is under test here."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class _RecordingWhatsAppSender:
    """A CONFIGURED WhatsApp channel, so "nobody could send it" is never why a test passes.

    If the consent gate ever regressed, this sender is standing right there, ready and able to
    message a real phone number. That is precisely why it is configured.

    ``caps`` is required of any PHONE sender (the registry's value type is ``PhoneChannelSender``,
    which is how fail-closed is expressed as a type rather than a comment). The ceiling here is
    generous on purpose: a daily cap must never be the accidental reason one of these tests passes
    — the thing under test is CONSENT.
    """

    channel = Channel.WHATSAPP

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.caps = DailyCaps(per_phone=100, per_ip=100)

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        del subject
        self.sent.append((to, body))


@pytest_asyncio.fixture
async def wired_client(app: FastAPI, client: AsyncClient) -> AsyncClient:
    app.include_router(bookings.router)
    app.include_router(event_types.router)
    return client


async def _seed(app: FastAPI, *, phone_rule_active: bool | None) -> dict[str, Any]:
    """A tenant with one event type and (optionally) an ACTIVE/paused WhatsApp reminder rule.

    ``phone_rule_active=None`` seeds NO phone rule at all — the self-hoster who only ever sends
    e-mail, and whose guests must therefore never be asked for a phone number.
    """
    sessionmaker: Sessionmaker = app.state.sessionmaker
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

        if phone_rule_active is not None:
            session.add(
                WorkflowTemplate(
                    tenant_id=tenant.id,
                    channel=Channel.WHATSAPP.value,
                    kind="reminder",
                    locale="es",
                    body="Hola {{guest_name}}, te esperamos.",
                )
            )
            workflow = Workflow(
                tenant_id=tenant.id,
                event_type_id=None,  # tenant-wide
                name="whatsapp reminder",
                trigger=WorkflowTrigger.BEFORE_START.value,
                offset_minutes=-1440,
                active=phone_rule_active,
            )
            session.add(workflow)
            await session.flush()
            session.add(
                WorkflowStep(
                    tenant_id=tenant.id,
                    workflow_id=workflow.id,
                    channel=Channel.WHATSAPP.value,
                    kind="reminder",
                    position=0,
                )
            )
        await session.flush()
        _, full_key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")

        now = datetime.now(UTC)
        # Two days out, so the BEFORE_START (-1440 min) step has a send time in the FUTURE and is a
        # real pending row — not one skipped for arriving after the fact.
        target = (now + timedelta(days=2)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=target,
            window_to=target,
            now=now,
        )
        assert result is not None and result.slots
        return {
            "headers": {"Authorization": f"Bearer {full_key}"},
            "tenant_id": tenant.id,
            "event_type_id": str(event_type.id),
            "slot": result.slots[0].start.isoformat(),
            "start": result.slots[0].start,
        }


def _payload(seeded: dict[str, Any], **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "event_type_id": seeded["event_type_id"],
        "start": seeded["slot"],
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "UTC",
    }
    body.update(over)
    return body


async def _stored(app: FastAPI, booking_id: str) -> Booking:
    """The booking as PostgreSQL actually holds it — the effective state, not the API's own echo."""
    sessionmaker: Sessionmaker = app.state.sessionmaker
    async with sessionmaker() as session:
        row = await session.scalar(select(Booking).where(Booking.id == uuid.UUID(booking_id)))
    assert row is not None
    return row


async def _drain(app: FastAPI, *, start: datetime) -> tuple[_RecordingWhatsAppSender, list[Outbox]]:
    """Drain the outbox at reminder time with a WORKING WhatsApp channel; report what happened."""
    sessionmaker: Sessionmaker = app.state.sessionmaker
    whatsapp = _RecordingWhatsAppSender()
    execute = make_booking_effect_executor(
        sessionmaker=sessionmaker,
        sender=_RecordingEmailSender(),
        service_factory=None,
        channels={Channel.WHATSAPP: whatsapp},
    )
    await drain_outbox(sessionmaker, now=start - timedelta(hours=24), execute=execute)
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.effect == OutboxEffect.NOTIFY.value)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
    return whatsapp, rows


def _whatsapp_step(rows: list[Outbox]) -> Outbox:
    return next(row for row in rows if row.payload["channel"] == Channel.WHATSAPP.value)


# --------------------------------------------------------------------------------------
# What the booking page is TOLD to ask for. ``collects_phone`` is the server's answer to "will
# anything actually message this number?" — and it is what makes the field appear at all.
# --------------------------------------------------------------------------------------


async def test_event_types_report_no_phone_needed_when_no_phone_rule_exists(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """The e-mail-only self-hoster. Their guests are never asked for a phone at all (RNF-8)."""
    seeded = await _seed(app, phone_rule_active=None)

    response = await wired_client.get(EVENT_TYPES, headers=seeded["headers"])

    assert response.status_code == 200
    assert response.json()[0]["collects_phone"] is False


async def test_event_types_report_a_phone_is_needed_when_a_whatsapp_rule_is_active(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.get(EVENT_TYPES, headers=seeded["headers"])

    assert response.status_code == 200
    assert response.json()[0]["collects_phone"] is True


async def test_a_paused_phone_rule_stops_asking_for_the_phone(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """Switch the rule off and the question goes with it: nothing will send, so nothing is asked."""
    seeded = await _seed(app, phone_rule_active=False)

    response = await wired_client.get(EVENT_TYPES, headers=seeded["headers"])

    assert response.status_code == 200
    assert response.json()[0]["collects_phone"] is False


# --------------------------------------------------------------------------------------
# The full path: the guest ticks the box → the column is sealed → the message goes out. And the
# guest who does NOT tick it → the column stays NULL → the channel skips, with its own reason.
# --------------------------------------------------------------------------------------


async def test_consent_given_seals_the_column_and_the_whatsapp_step_sends(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """The happy path, asserted where it counts: in the column, and at the sender.

    This half has to be asserted or the refusals below pass for free — a system that sent NOTHING,
    ever, would satisfy every one of them. So this pins that a consented phone really IS messaged,
    and therefore that the refusals below are refusals rather than paralysis.

    It is the whole chain in one test: a checkbox ticked on a public form becomes a stamped column
    in Postgres, which unlocks a real WhatsApp send at drain time, to that exact number.
    """
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.post(
        BOOKINGS,
        json=_payload(seeded, guest_phone="+1 (305) 413-1728", guest_phone_consent=True),
        headers=seeded["headers"],
    )
    assert response.status_code == 201

    stored = await _stored(app, response.json()["id"])
    assert stored.guest_phone == "+13054131728"  # normalized to E.164 on the way in
    assert stored.guest_phone_consent_at is not None, (
        "the guest ticked the box and the column is NULL: the consent was thrown away"
    )
    # PostgreSQL keeps the zone (``DateTime(timezone=True)``): the stamp is a real instant, and it
    # is the SERVER's clock — never a timestamp the client supplied.
    assert stored.guest_phone_consent_at.tzinfo is not None

    whatsapp, rows = await _drain(app, start=seeded["start"])

    assert whatsapp.sent, "a consented guest was never messaged — the gate is refusing everything"
    assert whatsapp.sent[0][0] == "+13054131728", "the message went to a number nobody consented to"
    assert _whatsapp_step(rows).status == "delivered"


async def test_no_consent_leaves_the_column_null_and_the_channel_skips(
    app: FastAPI, wired_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """==The legal one.== The number is real, the channel is configured, the guest never agreed.

    Nothing is sent, the column is NULL, and the step is skipped with the reason that names WHY —
    ``no-phone-consent``, distinguishable from "the channel is not configured", because "could not
    send" and "was not allowed to send" are different facts an operator has to tell apart.
    """
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.post(
        BOOKINGS,
        # A number typed with the box left unticked. This is a REAL state, not an error.
        json=_payload(seeded, guest_phone="+13054131728"),
        headers=seeded["headers"],
    )
    assert response.status_code == 201

    stored = await _stored(app, response.json()["id"])
    assert stored.guest_phone == "+13054131728"
    assert stored.guest_phone_consent_at is None, "a consent nobody gave was stamped anyway"

    with caplog.at_level("WARNING"):
        whatsapp, rows = await _drain(app, start=seeded["start"])

    assert whatsapp.sent == [], "an unconsented message was sent to a real phone number"
    assert _whatsapp_step(rows).status == "skipped"
    messages = [record.getMessage() for record in caplog.records]
    assert any("no-phone-consent" in message for message in messages)


async def test_booking_with_no_phone_at_all_still_confirms(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """The phone is optional: a guest who gives none books normally. It never blocks a booking."""
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.post(BOOKINGS, json=_payload(seeded), headers=seeded["headers"])

    assert response.status_code == 201
    stored = await _stored(app, response.json()["id"])
    assert stored.guest_phone is None
    assert stored.guest_phone_consent_at is None

    whatsapp, rows = await _drain(app, start=seeded["start"])
    assert whatsapp.sent == []
    assert _whatsapp_step(rows).status == "skipped"  # nothing to message: skipped, not retried


async def test_consent_without_a_number_is_refused_at_the_edge(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """Consent referencing no number consents to nothing. 422 — never a silently ignored field."""
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.post(
        BOOKINGS, json=_payload(seeded, guest_phone_consent=True), headers=seeded["headers"]
    )

    assert response.status_code == 422


async def test_a_malformed_phone_is_refused_at_the_edge(
    app: FastAPI, wired_client: AsyncClient
) -> None:
    """No country code, and no guessing one: a number we cannot address is not stored as if we
    could."""
    seeded = await _seed(app, phone_rule_active=True)

    response = await wired_client.post(
        BOOKINGS,
        json=_payload(seeded, guest_phone="305-413-1728", guest_phone_consent=True),
        headers=seeded["headers"],
    )

    assert response.status_code == 422
