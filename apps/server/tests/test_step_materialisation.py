"""The step PRODUCER: a booking's workflow steps are queued inside the booking's own transaction.

This is what carries RF-10 now that the APScheduler reminder is gone. Retiring that scheduler
without this would leave every NEW booking with no reminder at all — the migration only rescues
the bookings that were already live, so ``main`` would ship a real functional regression: book,
and never be reminded.

The four transitions are exercised end-to-end through the real booking service, against the schema
the REAL migration builds (not ``create_all``), so "a booking made after the migration gets its
reminder" is asserted against the thing that actually runs in production.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aethercal.core.model import BookingStatus
from aethercal.server.channels import Channel
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.migrate import run_migrations
from aethercal.server.db.models import (
    Booking,
    EventType,
    ExternalCalendarLink,
    Outbox,
    Schedule,
    SentNotification,
    Tenant,
    User,
)
from aethercal.server.db.models.workflows import Workflow, WorkflowStep, WorkflowTrigger
from aethercal.server.integrations.messaging.guard import DailyCaps
from aethercal.server.services.bookings import (
    BookingParams,
    cancel_booking,
    create_booking,
    mark_no_show,
    reschedule_booking,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.outbox import (
    OutboxEffect,
    drain_outbox,
    make_booking_effect_executor,
)
from aethercal.server.services.workflows import seed_default_workflows

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}
_NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)  # a week out: comfortably outside the 24 h window
_LATER = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def migrated(tmp_path: Path) -> AsyncIterator[Sessionmaker]:
    """A database built by the REAL migration chain, not by ``create_all``.

    That is the point of the fixture: the reminder rule is seeded by migration 0005, so a test that
    built its schema from the models would be asserting against a world that does not exist.
    """
    path = tmp_path / "materialisation.sqlite"
    sync_engine = sa.create_engine(f"sqlite:///{path}")
    run_migrations(sync_engine)
    sync_engine.dispose()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Tenant, EventType]:
    """A tenant created AFTER the migration — so its reminder rule comes from the tenant-creation
    seeding, not from 0005 (which only ever saw the tenants that already existed)."""
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Acme")
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
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 60,
    )
    session.add(event_type)
    await session.flush()
    await seed_default_workflows(session, tenant_id=tenant.id)
    return tenant, event_type


def _params(event_type_id: uuid.UUID, start: datetime) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )


async def _steps(session: AsyncSession, booking_id: uuid.UUID) -> list[Outbox]:
    return list(
        (
            await session.scalars(
                select(Outbox).where(
                    Outbox.booking_id == booking_id, Outbox.effect == OutboxEffect.NOTIFY.value
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------------------
# The regression the merge was blocked on.
# --------------------------------------------------------------------------------------


async def test_a_booking_created_after_the_migration_gets_its_reminder(
    migrated: Sessionmaker,
) -> None:
    """The blocker. The APScheduler jobstore is gone; if nothing materialises the reminder inside
    the booking's transaction, a guest books and is simply never reminded."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session:
        steps = await _steps(session, booking_id)

    assert len(steps) == 1, "the new booking got NO reminder"
    step = steps[0]
    assert step.payload["trigger"] == WorkflowTrigger.BEFORE_START.value
    assert step.payload["kind"] == "reminder"
    assert step.payload["channel"] == "email"
    assert step.status == "pending"
    # Due 24 h before the start — the outbox's next_retry_at IS the send time.
    assert step.next_retry_at is not None
    assert step.next_retry_at.replace(tzinfo=UTC) == _SLOT - timedelta(hours=24)


async def test_the_step_commits_atomically_with_its_booking(migrated: Sessionmaker) -> None:
    """It is queued in the booking's OWN transaction, so a rolled-back booking cannot leave a step
    behind — the same guarantee the webhook and the email intent already have."""
    async with migrated() as session:
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id
        assert len(await _steps(session, booking_id)) == 1
        await session.rollback()

    async with migrated() as session:
        assert await session.get(Booking, booking_id) is None
        assert await _steps(session, booking_id) == []


async def test_a_booking_inside_the_reminder_window_is_skipped_not_sent_late(
    migrated: Sessionmaker,
) -> None:
    """Booked less than 24 h out: the send time is already past. A ``next_retry_at`` in the past
    drains IMMEDIATELY, so a "reminder" would land after the fact — noise. It is skipped."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        soon = _NOW + timedelta(hours=3)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, soon), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session:
        assert await _steps(session, booking_id) == []


# --------------------------------------------------------------------------------------
# The transition table, driven through the real service.
# --------------------------------------------------------------------------------------


async def test_rescheduling_voids_the_predecessors_steps_and_materialises_the_successors(
    migrated: Sessionmaker,
) -> None:
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        first = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        old_id = first.id
        second = await reschedule_booking(
            session, tenant_id=tenant.id, booking_id=old_id, new_start=_LATER, now=_NOW
        )
        new_id = second.id

    async with migrated() as session:
        old_steps = await _steps(session, old_id)
        new_steps = await _steps(session, new_id)

    # The replaced booking's step is RETIRED — it can never come due again.
    assert [step.status for step in old_steps] == ["voided"]
    assert old_steps[0].next_retry_at is None
    # The successor is a NEW row, so its step is a plain INSERT, timed off the NEW start.
    assert len(new_steps) == 1
    assert new_steps[0].status == "pending"
    assert new_steps[0].next_retry_at is not None
    assert new_steps[0].next_retry_at.replace(tzinfo=UTC) == _LATER - timedelta(hours=24)


async def test_cancelling_retires_the_pending_reminder(migrated: Sessionmaker) -> None:
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id
        await cancel_booking(session, tenant_id=tenant.id, booking_id=booking_id, now=_NOW)

    async with migrated() as session:
        steps = await _steps(session, booking_id)

    assert [step.status for step in steps] == ["voided"], (
        "a cancelled booking kept a live reminder — the guest would be reminded of a booking that "
        "no longer exists"
    )


async def test_a_no_show_voids_the_pending_after_end_follow_up(migrated: Sessionmaker) -> None:
    """Otherwise the guest who did NOT show up receives "thanks for meeting with us"."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        tenant_id = tenant.id
        follow_up = Workflow(
            tenant_id=tenant_id,
            event_type_id=None,
            name="follow-up",
            trigger=WorkflowTrigger.AFTER_END.value,
            offset_minutes=60,
            active=True,
        )
        session.add(follow_up)
        await session.flush()
        session.add(
            WorkflowStep(
                tenant_id=tenant_id,
                workflow_id=follow_up.id,
                channel="email",
                kind="follow_up",
                position=0,
            )
        )
        await session.flush()

        booking = await create_booking(
            session, tenant_id=tenant_id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    async with migrated() as session, session.begin():
        queued = await _steps(session, booking_id)
        assert {step.payload["trigger"] for step in queued} == {
            WorkflowTrigger.BEFORE_START.value,
            WorkflowTrigger.AFTER_END.value,
        }
        await mark_no_show(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            now=_SLOT + timedelta(days=1),
        )

    async with migrated() as session:
        by_trigger = {step.payload["trigger"]: step for step in await _steps(session, booking_id)}
        booking = await session.get(Booking, booking_id)

    assert by_trigger[WorkflowTrigger.AFTER_END.value].status == "voided", (
        "the no-show kept its follow-up: the guest who never showed up gets 'thanks for meeting'"
    )
    assert booking is not None and booking.status is BookingStatus.NO_SHOW


# --------------------------------------------------------------------------------------
# The write-target rule belongs to the DATABASE, not to the code.
# --------------------------------------------------------------------------------------


async def test_a_connection_cannot_have_two_booking_targets(migrated: Sessionmaker) -> None:
    """ "Which calendar do we write to?" must never be answered by whichever row was read first. The
    partial unique index turns a second write target into an INTEGRITY ERROR."""
    async with migrated() as session, session.begin():
        tenant, _event_type = await _seed(session)
        tenant_id = tenant.id
        host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).one()
        connection = await store_google_connection(
            session,
            tenant_id=tenant_id,
            user_id=host.id,
            credential=GoogleCredential(account_email="h@gmail.com", token_json='{"token": "t"}'),
            fernet=Fernet(derive_fernet_key("test-app-secret")),
        )
        await session.flush()
        connection_id = connection.id
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="primary",
                is_booking_target=True,
            )
        )
        await session.flush()

    async with migrated() as session, session.begin():
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="second-calendar",
                is_booking_target=True,  # a SECOND write target on the same connection
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()

    # A second NON-target calendar on the same connection is fine — the index is partial.
    async with migrated() as session, session.begin():
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant_id,
                connection_id=connection_id,
                external_calendar_id="second-calendar",
                is_booking_target=False,
            )
        )
        await session.flush()


# --------------------------------------------------------------------------------------
# END TO END. Not "the step was queued" — the guest actually receives the email.
# --------------------------------------------------------------------------------------


class _RecordingSender:
    """A fake :class:`EmailSender` — the exact seam ``SmtpEmailSender`` implements."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def test_book_then_drain_actually_sends_the_reminder_email(migrated: Sessionmaker) -> None:
    """The whole point, end to end: create a booking, drain the outbox, and the REMINDER EMAIL GOES
    OUT — with its ledger row.

    Queueing a step is worthless if nothing can execute it. Materialising reminders as ``notify``
    while the executor raised ``NotImplementedError`` was WORSE than the regression it replaced:
    every reminder would fail six times with backoff and dead-letter — noise in the backlog, and the
    guest still gets no email.
    """
    sender = _RecordingSender()

    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        booking = await create_booking(
            session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    execute = make_booking_effect_executor(
        sessionmaker=migrated, sender=sender, service_factory=None
    )

    # Not due yet: the reminder is scheduled for start - 24 h.
    assert (await drain_outbox(migrated, now=_NOW, execute=execute)).attempted == 0
    assert sender.sent == []

    # At its send time, it drains — and the email is really sent.
    due = _SLOT - timedelta(hours=24)
    report = await drain_outbox(migrated, now=due, execute=execute)

    assert len(report.delivered) == 1, f"the reminder did not deliver: {report}"
    assert report.dead == [] and report.failed == []
    assert len(sender.sent) == 1, "the guest received NO reminder email"
    message = sender.sent[0]
    assert "ada@example.com" in str(message["To"])
    # It went through the real composer, so it carries the .ics invite — which is the whole reason
    # the email channel does NOT go through the plain-body ChannelSender wrapper.
    assert any(part.get_content_type() == "text/calendar" for part in message.walk()), (
        "the reminder lost its .ics invite"
    )

    async with migrated() as session:
        ledger = list((await session.scalars(select(SentNotification))).all())
        step = (await _steps(session, booking_id))[0]

    assert step.status == "delivered"
    # The ledger row is what stops a second send — keyed on (kind, channel, step).
    assert len(ledger) == 1
    assert ledger[0].booking_id == booking_id
    assert ledger[0].kind == "reminder"
    assert ledger[0].channel == "email"
    assert ledger[0].step_id == uuid.UUID(str(step.payload["step_id"]))

    # A re-drain sends nothing: the ledger, not luck, is what makes it exactly-once.
    again = await drain_outbox(migrated, now=due + timedelta(hours=1), execute=execute)
    assert again.attempted == 0
    assert len(sender.sent) == 1


async def test_a_step_on_an_unconfigured_channel_is_skipped_not_dead_lettered(
    migrated: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """A channel with no credentials is a DISABLED FEATURE, not an error.

    Failing it would burn six attempts of backoff and dead-letter — the queue fills with noise and
    the message still does not arrive. It is retired with its reason instead, in ONE attempt.
    """
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        tenant_id = tenant.id
        whatsapp = Workflow(
            tenant_id=tenant_id,
            event_type_id=None,
            name="whatsapp reminder",
            trigger=WorkflowTrigger.BEFORE_START.value,
            offset_minutes=-1440,
            active=True,
        )
        session.add(whatsapp)
        await session.flush()
        session.add(
            WorkflowStep(
                tenant_id=tenant_id,
                workflow_id=whatsapp.id,
                channel="whatsapp",  # nothing on this instance can send it
                kind="reminder",
                position=0,
            )
        )
        await session.flush()
        booking = await create_booking(
            session, tenant_id=tenant_id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking_id = booking.id

    sender = _RecordingSender()
    execute = make_booking_effect_executor(
        sessionmaker=migrated, sender=sender, service_factory=None
    )
    due = _SLOT - timedelta(hours=24)
    with caplog.at_level("WARNING"):
        report = await drain_outbox(migrated, now=due, execute=execute)

    # The email step delivered; the WhatsApp step was SKIPPED — not failed, not dead.
    assert len(report.delivered) == 1
    assert len(report.skipped) == 1
    assert report.failed == [] and report.dead == []
    assert len(sender.sent) == 1  # the email still went out

    async with migrated() as session:
        by_channel = {step.payload["channel"]: step for step in await _steps(session, booking_id)}
    assert by_channel["email"].status == "delivered"
    assert by_channel["whatsapp"].status == "skipped"
    assert by_channel["whatsapp"].attempts == 0, "a disabled channel burned a retry attempt"
    assert by_channel["whatsapp"].next_retry_at is None, "a disabled channel is still scheduled"

    assert any(
        "SKIPPED" in record.getMessage() and "whatsapp" in record.getMessage()
        for record in caplog.records
    ), "the skip was silent — an absent message is exactly what nobody notices"

    # And it stays out of the queue: a later drain never touches it again.
    assert (
        await drain_outbox(migrated, now=due + timedelta(days=1), execute=execute)
    ).attempted == 0


# --------------------------------------------------------------------------------------
# CONSENT. Persisting it and then not reading it is worse than not persisting it at all:
# the column looks like a safeguard, and the unconsented message goes out anyway.
# --------------------------------------------------------------------------------------


async def _booking_with_whatsapp_step(
    migrated: Sessionmaker, *, phone: str | None, consented_at: datetime | None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A booking with a WhatsApp reminder step, and whatever phone/consent state we are testing."""
    async with migrated() as session, session.begin():
        tenant, event_type = await _seed(session)
        tenant_id = tenant.id
        whatsapp = Workflow(
            tenant_id=tenant_id,
            event_type_id=None,
            name="whatsapp reminder",
            trigger=WorkflowTrigger.BEFORE_START.value,
            offset_minutes=-1440,
            active=True,
        )
        session.add(whatsapp)
        await session.flush()
        session.add(
            WorkflowStep(
                tenant_id=tenant_id,
                workflow_id=whatsapp.id,
                channel="whatsapp",
                kind="reminder",
                position=0,
            )
        )
        await session.flush()
        booking = await create_booking(
            session, tenant_id=tenant_id, params=_params(event_type.id, _SLOT), now=_NOW
        )
        booking.guest_phone = phone
        booking.guest_phone_consent_at = consented_at
        await session.flush()
        return tenant_id, booking.id


class _RecordingChannelSender:
    """A configured WhatsApp sender, so "nobody could send it" is NEVER why a test passes.

    It carries ``caps`` because a PHONE sender without them is unrepresentable: the registry's value
    type is ``PhoneChannelSender``, which is how "fail-closed" is expressed as a type rather than as
    a comment. A generous ceiling here, so the CAP is never the accidental reason a test goes green
    — the tests that mean to exercise the cap set it themselves."""

    channel = Channel.WHATSAPP

    def __init__(self, caps: DailyCaps | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.caps = caps or DailyCaps(per_phone=100, per_ip=100)

    async def send(self, *, to: str, subject: str | None, body: str) -> None:
        self.sent.append((to, body))


async def _drain_whatsapp(
    migrated: Sessionmaker, booking_id: uuid.UUID, whatsapp: _RecordingChannelSender
) -> Outbox:
    execute = make_booking_effect_executor(
        sessionmaker=migrated,
        sender=_RecordingSender(),
        service_factory=None,
        channels={Channel.WHATSAPP: whatsapp},
    )
    await drain_outbox(migrated, now=_SLOT - timedelta(hours=24), execute=execute)
    async with migrated() as session:
        steps = await _steps(session, booking_id)
    return next(step for step in steps if step.payload["channel"] == "whatsapp")


async def test_a_phone_WITHOUT_consent_is_never_messaged(
    migrated: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """==The legal one.== We have the number, the channel IS configured, and the guest never agreed.
    Sending anyway is not a style problem. The step is skipped with its OWN reason — not the same
    one as "the channel is not configured", because "could not send" and "not allowed to send" are
    different facts and an operator has to be able to tell them apart."""
    _tenant_id, booking_id = await _booking_with_whatsapp_step(
        migrated, phone="+13055551234", consented_at=None
    )
    whatsapp = _RecordingChannelSender()

    with caplog.at_level("WARNING"):
        step = await _drain_whatsapp(migrated, booking_id, whatsapp)

    assert whatsapp.sent == [], "an unconsented message was sent to a real phone number"
    assert step.status == "skipped"
    assert step.attempts == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any("no-phone-consent" in message for message in messages), (
        "the skip reason must be distinguishable from 'channel not configured'"
    )
    assert not any("channel-unconfigured" in message for message in messages)


async def test_a_phone_WITH_consent_is_ACTUALLY_MESSAGED(
    migrated: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """The other half of the gate, and it has to be asserted or the tests above pass for free — a
    handler that skipped EVERYTHING would satisfy every one of them.

    This test used to stop at ``no-template-renderer``: consent opened the gate, and the step then
    hit the missing renderer. ==That renderer has landed, so the promise this test was holding open
    is now due:== with consent recorded and the channel configured, the guest is REALLY messaged.
    The body is rendered from the built-in template, the step settles ``delivered``, and the ledger
    row that makes it exactly-once is written with its (kind, channel, step) identity.

    A version of this test that still accepted ``skipped`` would be the silent no-op in its purest
    form: green, and hiding the fact that no message ever goes out."""
    _tenant_id, booking_id = await _booking_with_whatsapp_step(
        migrated, phone="+13055551234", consented_at=_NOW
    )
    whatsapp = _RecordingChannelSender()

    with caplog.at_level("WARNING"):
        step = await _drain_whatsapp(migrated, booking_id, whatsapp)

    assert step.status == "delivered", f"the consented guest was never messaged: {step.status}"
    assert len(whatsapp.sent) == 1, "no WhatsApp message reached the sender"
    recipient, body = whatsapp.sent[0]
    assert recipient == "+13055551234"
    # A REAL body, rendered from the built-in reminder template — not an empty string, and not a
    # template with its holes still in it.
    assert body.strip(), "an EMPTY body was sent to a real phone number"
    assert "{{" not in body, f"an unsubstituted placeholder shipped to a guest: {body!r}"

    messages = [record.getMessage() for record in caplog.records]
    assert not any("no-phone-consent" in message for message in messages)
    assert not any("no-template" in message for message in messages)

    # The ledger row is what stops a second send — keyed on (kind, channel, step).
    async with migrated() as session:
        ledger = list(
            (
                await session.scalars(
                    select(SentNotification).where(SentNotification.channel == "whatsapp")
                )
            ).all()
        )
    assert len(ledger) == 1
    assert ledger[0].step_id == uuid.UUID(str(step.payload["step_id"]))

    # A re-drain sends nothing: the ledger, not luck, is what makes it exactly-once.
    again = await _drain_whatsapp(migrated, booking_id, whatsapp)
    assert again.status == "delivered"
    assert len(whatsapp.sent) == 1, "the guest was messaged twice"


async def test_WITHDRAWN_consent_closes_the_gate_again(
    migrated: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """Revocation needs no special code path: it IS the absence of the stamp. Set the column back to
    NULL and the same gate closes, automatically."""
    _tenant_id, booking_id = await _booking_with_whatsapp_step(
        migrated, phone="+13055551234", consented_at=_NOW
    )
    async with migrated() as session, session.begin():
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        booking.guest_phone_consent_at = None  # the guest withdraws consent

    whatsapp = _RecordingChannelSender()
    with caplog.at_level("WARNING"):
        step = await _drain_whatsapp(migrated, booking_id, whatsapp)

    assert whatsapp.sent == [], "a message went out after consent was withdrawn"
    assert step.status == "skipped"
    assert any("no-phone-consent" in record.getMessage() for record in caplog.records)


async def test_no_phone_at_all_has_its_own_distinct_reason(
    migrated: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """ "No number" and "no consent" are also different facts, and both differ from "no channel"."""
    _tenant_id, booking_id = await _booking_with_whatsapp_step(
        migrated, phone=None, consented_at=None
    )
    whatsapp = _RecordingChannelSender()

    with caplog.at_level("WARNING"):
        step = await _drain_whatsapp(migrated, booking_id, whatsapp)

    assert whatsapp.sent == []
    assert step.status == "skipped"
    messages = [record.getMessage() for record in caplog.records]
    assert any("no-phone:" in message for message in messages)
    assert not any("no-phone-consent" in message for message in messages)
