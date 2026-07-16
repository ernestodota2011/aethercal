"""THE SILENCE OF AN UNPAID HOLD (B-05a) — a booking that was never CONFIRMED emits nothing.

We are about to start writing ``BookingStatus.PENDING`` (a hold awaiting payment). Today nobody
writes that status, and three independent paths would announce an appointment **nobody has paid
for**: ``apply_booking_transition`` (which never reads ``booking.status`` at all), the unconditional
``enqueue_event("booking.created")``, and a workflow-rule edit re-materialising steps for a live
hold. A Google Calendar event with the guest as an attendee makes **Google** mail them the
invitation, so an unpaid hold would announce itself.

The rule this wave installs: ==a booking that has never been CONFIRMED produces not one outbound.==

The belt goes in the **FUNNELS**, never in a caller — ``Outbox(...)`` is constructed in exactly ONE
place in the whole source tree (``enqueue_effect``) and ``WebhookDelivery(...)`` in exactly one
(``enqueue_event``). So the tests below call the **funnel directly** with a synthetic PENDING
booking. Going through ``create_booking`` would only prove that the one caller we happened to fix is
fixed — and the next enqueue path nobody foresaw would sail straight past.

Covers §6 criteria 20, 20b, 20c, 20d, 21, 22, 23 and 23b of the Tanda-B spec.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.schemas.webhooks import WEBHOOK_EVENTS
from aethercal.schemas.workflows import WorkflowCreate, WorkflowStepIn
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
from aethercal.server.db.pools import WorkerPools
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.bookings import (
    BookingParams,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import (
    Confirmation,
    GoogleOperation,
    OutboxEffect,
    Suppressed,
    as_utc,
    confirmation_policy,
    drain_outbox,
    email_dedupe_key,
    enqueue_effect,
    google_dedupe_key,
    make_booking_effect_executor,
)
from aethercal.server.services.webhooks import WebhookSubject, enqueue_event, event_subject
from aethercal.server.services.workflow_rules import create_workflow

_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}

# 2026-07-06 is a Monday; midnight before it opens leaves every weekday slot bookable.
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)
_SLOT_9 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_SLOT_11 = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)


def _pools(maker: async_sessionmaker[AsyncSession]) -> WorkerPools:
    """Both of the drain's pools over ONE offline sessionmaker (SQLite has no roles to split)."""
    return WorkerPools.for_offline_tests(maker)


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, EventType]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5)
    session.add(schedule)
    await session.flush()
    event_type = await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        ),
    )
    return tenant, event_type


def _stamp(booking: Booking) -> datetime:
    """``booking.confirmed_at``, normalised — SQLite drops tzinfo on the round-trip (``as_utc``)."""
    assert booking.confirmed_at is not None
    return as_utc(booking.confirmed_at)


def _params(event_type_id: uuid.UUID, start: datetime) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )


async def _hold(session: AsyncSession, tenant: Tenant, event_type: EventType) -> Booking:
    """A synthetic UNPAID HOLD: ``PENDING``, never confirmed, ``confirmed_at IS NULL``.

    Written straight to the table on purpose. No writer of ``PENDING`` exists yet — that is B-05b —
    and the whole point of this wave is that the belt is already ON and PROVEN when that writer
    arrives. A test that could only build a hold through ``create_booking`` could not test the belt
    at all, because ``create_booking`` does not make holds today.
    """
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_SLOT_9,
        end_at=_SLOT_9 + timedelta(minutes=30),
        status=BookingStatus.PENDING,
        confirmed_at=None,  # the hold has not been paid for: nobody has been told it exists
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return booking


async def _outbox_of(session: AsyncSession, booking: Booking) -> list[Outbox]:
    return list(
        (await session.scalars(select(Outbox).where(Outbox.booking_id == booking.id))).all()
    )


def _payload_for(effect: OutboxEffect) -> dict[str, object]:
    """A representative payload per effect — enough for the funnel, which does not read it."""
    match effect:
        case OutboxEffect.EMAIL:
            return {"kind": "confirmation", "sequence": 0}
        case OutboxEffect.GOOGLE:
            return {"operation": GoogleOperation.UPSERT.value}
        case OutboxEffect.NOTIFY:
            return {"trigger": "on_booking", "channel": "whatsapp", "kind": "reminder"}
        case OutboxEffect.REFUND:
            return {"provider": "stripe", "provider_ref": "pi_test_NOT_A_REAL_KEY_x"}
        case OutboxEffect.EXPIRE_HOLD:
            return {"booking_id": "00000000-0000-0000-0000-000000000000"}


def _dedupe_for(effect: OutboxEffect) -> str:
    match effect:
        case OutboxEffect.EMAIL:
            return email_dedupe_key(NotificationKind.CONFIRMATION)
        case OutboxEffect.GOOGLE:
            return google_dedupe_key(GoogleOperation.UPSERT)
        case OutboxEffect.NOTIFY:
            return "wf:test:step:whatsapp"
        case OutboxEffect.REFUND:
            return "refund:pi_test_NOT_A_REAL_KEY_x"
        case OutboxEffect.EXPIRE_HOLD:
            return "expire_hold:00000000-0000-0000-0000-000000000000"


# --------------------------------------------------------------------------------------
# The EFFECT funnel — criterion 20 / 20b / 20c / 20d.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("effect", list(OutboxEffect))
@pytest.mark.asyncio
async def test_an_unpaid_hold_queues_no_CONFIRMATION_REQUIRING_effect(
    sqlite_session: AsyncSession, tenant_factory: Any, effect: OutboxEffect
) -> None:
    """==Criterion 20 + 20c, together.== A booking that was never confirmed queues no effect that
    REQUIRES confirmation — and DOES queue the ones declared EXEMPT.

    Parametrised over the WHOLE enum (a new effect is classified the day it lands), and it branches
    on :func:`confirmation_policy` rather than a fixed list:

    * ``REQUIRES_CONFIRMATION`` (EMAIL / GOOGLE / NOTIFY) → **suppressed**, no row. ``GOOGLE`` is
      the one that looks harmless and is not: a calendar event carrying the guest as an attendee
      makes **Google** send the invitation, so an unpaid hold reaching this funnel would announce
      itself through a channel we do not even own.
    * ``EXEMPT`` (REFUND / EXPIRE_HOLD, B-05b) → ==**queued** (criterion 20c)==. Their entire reason
      for existing is to act on a booking that was never confirmed — a belt that silenced them would
      keep the guest's money and leave the slot blocked for ever.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    hold = await _hold(sqlite_session, tenant, event_type)

    outcome = await enqueue_effect(
        sqlite_session,
        booking=hold,
        effect=effect,
        dedupe_key=_dedupe_for(effect),
        payload=_payload_for(effect),
    )

    if confirmation_policy(effect) is Confirmation.EXEMPT:
        # The money effects act on unpaid/cancelled bookings BY DEFINITION — they come out.
        assert isinstance(outcome, Outbox)
        assert [row.effect for row in await _outbox_of(sqlite_session, hold)] == [effect.value]
    else:
        assert isinstance(outcome, Suppressed)
        assert outcome.booking_id == hold.id
        assert outcome.effect is effect
        assert await _outbox_of(sqlite_session, hold) == []


@pytest.mark.parametrize("effect", list(OutboxEffect))
@pytest.mark.asyncio
async def test_a_confirmed_booking_still_queues_every_effect(
    sqlite_session: AsyncSession, tenant_factory: Any, effect: OutboxEffect
) -> None:
    """==Criterion 23.== The belt costs a CONFIRMED booking nothing: its whole chain is untouched.

    The failure mode of a gate is not only "it let something through" — it is "it silenced
    everything". This is the half that proves there is no regression.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_11), now=_BEFORE
    )

    outcome = await enqueue_effect(
        sqlite_session,
        booking=booking,
        effect=effect,
        # ``_dedupe_for``, not an arbitrary ``probe:`` string. A RETAINED effect's dedupe_key
        # outlives a guest erasure verbatim, so the funnel now refuses one it cannot rebuild from
        # the (PII-free) payload — see RETAINED_DEDUPE_KEY. A synthetic key is exactly the shape
        # that guard exists to catch, and this test has no reason to be the thing it catches: the
        # real key is what the product enqueues, and what this asserts about is unchanged.
        dedupe_key=_dedupe_for(effect),
        payload=_payload_for(effect),
    )

    assert isinstance(outcome, Outbox)
    assert outcome.booking_id == booking.id
    assert outcome.tenant_id == booking.tenant_id


@pytest.mark.asyncio
async def test_suppressed_by_a_hold_is_not_the_same_answer_as_already_queued(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 20d.== "Suppressed, unpaid" and "already queued" are DIFFERENT answers.

    ``None`` already means something precise here: *a terminal row owns this dedupe key* — and
    ``reconcile_workflow_steps`` reads it that way, in writing, to decide it must not re-send a
    message that has had its moment. Return ``None`` for a suppression too and the two collapse:
    "we refused to announce an unpaid hold" would be recorded as "the guest already got it", and
    ``report.materialised`` would be telling the truth about neither.

    So the suppression gets its own answer, and this test is what stops anyone collapsing them.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    hold = await _hold(sqlite_session, tenant, event_type)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_11), now=_BEFORE
    )
    key = email_dedupe_key(NotificationKind.CONFIRMATION)

    suppressed = await enqueue_effect(
        sqlite_session,
        booking=hold,
        effect=OutboxEffect.EMAIL,
        dedupe_key=key,
        payload=_payload_for(OutboxEffect.EMAIL),
    )
    # The confirmed booking already has its confirmation email (create_booking queued nothing
    # without effects, so queue one here), then ask for the very same key a second time.
    first = await enqueue_effect(
        sqlite_session,
        booking=booking,
        effect=OutboxEffect.EMAIL,
        dedupe_key=key,
        payload=_payload_for(OutboxEffect.EMAIL),
    )
    duplicate = await enqueue_effect(
        sqlite_session,
        booking=booking,
        effect=OutboxEffect.EMAIL,
        dedupe_key=key,
        payload=_payload_for(OutboxEffect.EMAIL),
    )

    assert isinstance(first, Outbox)
    assert duplicate is None  # "a terminal row already owns this key" — the dedupe conflict
    assert isinstance(suppressed, Suppressed)  # "we refused to speak for an unpaid hold"
    assert suppressed is not None  # ...and the two are NOT the same answer


# --------------------------------------------------------------------------------------
# The EVENT funnel — criterion 20 (the webhook half, which carries PII).
# --------------------------------------------------------------------------------------


async def _subscribe_to_everything(session: AsyncSession, tenant: Tenant) -> Webhook:
    row = Webhook(
        tenant_id=tenant.id,
        url="https://example.com/hook",
        secret=b"test-secret",
        events=list(WEBHOOK_EVENTS),
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_an_unpaid_hold_fans_out_no_webhook(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 20, the webhook half.== A hold nobody paid for reaches no subscriber.

    This one leaves the building with the guest's NAME, EMAIL and ANSWERS in the payload — a
    subscriber's CRM would file a lead for an appointment that does not exist, and no cancellation
    event would ever follow to retract it (criterion 21).

    The subscriber here is subscribed to EVERY event, so a silence in the assertion below cannot be
    an accident of what it happens to listen for.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_to_everything(sqlite_session, tenant)
    hold = await _hold(sqlite_session, tenant, event_type)

    deliveries = await enqueue_event(
        sqlite_session,
        booking=hold,
        event="booking.created",
        data={"id": str(hold.id), "guest_email": hold.guest_email},
        now=_BEFORE,
    )

    assert deliveries == []
    rows = (
        await sqlite_session.scalars(
            select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant.id)
        )
    ).all()
    assert list(rows) == []


@pytest.mark.asyncio
async def test_a_confirmed_booking_still_fans_its_webhook_out(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 23, the webhook half.== The subscriber of a REAL booking still hears about it."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    subscriber = await _subscribe_to_everything(sqlite_session, tenant)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_11), now=_BEFORE
    )

    deliveries = await enqueue_event(
        sqlite_session,
        booking=booking,
        event="booking.created",
        data={"id": str(booking.id)},
        now=_BEFORE,
    )

    assert len(deliveries) == 1
    assert deliveries[0].webhook_id == subscriber.id
    assert deliveries[0].tenant_id == tenant.id


def test_every_webhook_event_declares_whose_it_is() -> None:
    """The event funnel cannot evaluate the guard without knowing WHO the event is about.

    Its signature never carried a booking — only ``tenant_id`` and a free-form ``data`` dict — and
    the two obvious ways out were both wrong. Digging ``data["id"]`` out of the payload ties the
    gate to a dict whose shape is variable BY DESIGN: change the payload, and the gate opens in
    silence. Handing the guard back to the four callers is the mistake this wave exists to correct.

    So the funnel takes the ``Booking``, and this table is what keeps that honest: an event is
    declared to be ABOUT something, exhaustively (``assert_never``). The day one arrives that is
    NOT about a booking — a tenant-level event, say — it will not COMPILE until somebody decides
    what the silence rule means for it.
    """
    for event in WEBHOOK_EVENTS:
        assert event_subject(event) in tuple(WebhookSubject)

    # Every event today is about ONE appointment, and that is why the funnel can take a Booking.
    assert {event_subject(event) for event in WEBHOOK_EVENTS} == {WebhookSubject.BOOKING}


# --------------------------------------------------------------------------------------
# A rule edit does not arm a hold — criterion 22.
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editing_a_workflow_rule_does_not_materialise_steps_for_a_hold(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 22.== Writing a rule arms the CONFIRMED bookings on the books — never the holds.

    A new rule reconciles onto the bookings that already exist (a tenant who writes "remind 1 h
    before" this morning means it for the meeting already in the diary). ``_LIVE_STATUSES`` used to
    include ``PENDING``, so a hold was one of those governed bookings — and editing an unrelated
    reminder would materialise its steps, sending the guest who never paid a reminder for an
    appointment nobody ever confirmed to them.

    Both bookings sit in the same tenant so this is one reconcile pass making two different
    decisions: the confirmed booking gets its NOTIFY step, the hold gets nothing.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    hold = await _hold(sqlite_session, tenant, event_type)
    confirmed = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_11), now=_BEFORE
    )

    # A reminder one hour before the start — both bookings start at a future instant relative to
    # ``_BEFORE``, so a governed booking WOULD get a queued step.
    await create_workflow(
        sqlite_session,
        tenant_id=tenant.id,
        data=WorkflowCreate(
            name="1h reminder",
            trigger="before_start",
            offset_minutes=-60,
            event_type_id=event_type.id,
            steps=[WorkflowStepIn(channel="email", kind="reminder")],
        ),
        now=_BEFORE,
    )

    # The confirmed booking was armed; the hold was not touched.
    confirmed_steps = await _outbox_of(sqlite_session, confirmed)
    assert [row.effect for row in confirmed_steps] == [OutboxEffect.NOTIFY.value]
    assert await _outbox_of(sqlite_session, hold) == []


# --------------------------------------------------------------------------------------
# Cancelling / abandoning a hold — criteria 21 and 23b.
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelling_an_unpaid_hold_announces_nothing(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 21.== A hold that is abandoned frees its slot and announces NOTHING.

    ==You never announce the cancellation of an appointment you never announced the creation of.==
    Today a cancel emits ``booking.cancelled`` and runs ``apply_booking_transition(CANCEL)`` — which
    would tell a subscriber, and materialise the ``on_cancel`` workflow, for a booking nobody was
    ever told existed. The funnels would suppress both, but the cancel of a hold should not build
    the announcement at all: it is put down quietly.

    Proven through ``cancel_booking`` (not the funnel directly) because the SHORT-CIRCUIT is what
    this criterion is about — that the cancel operation itself declines to speak, not merely that
    the funnel catches it afterwards.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_to_everything(sqlite_session, tenant)
    hold = await _hold(sqlite_session, tenant, event_type)

    cancelled = await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=hold.id,
        now=_BEFORE + timedelta(minutes=5),
        effects=None,
    )

    # The slot is freed — that part of a cancel is real.
    assert cancelled.status is BookingStatus.CANCELLED
    # ...but nothing was announced: no webhook, and no queued effect of any kind.
    webhooks = (
        await sqlite_session.scalars(
            select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant.id)
        )
    ).all()
    assert list(webhooks) == []
    assert await _outbox_of(sqlite_session, hold) == []
    # And the SHORT-CIRCUIT ran, not merely the funnel afterwards: the cancel of a hold never builds
    # the announcement, so it never bumps the iCal sequence for an event that does not exist. A hold
    # is born at sequence 0 and a quiet cancel leaves it there. (Without the short-circuit, cancel
    # would run its full machinery — bumping this to 1 — only for the funnels to throw the output
    # away.)
    assert cancelled.sequence == 0


@pytest.mark.asyncio
async def test_cancelling_a_confirmed_booking_still_announces_its_cancellation(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 23, cancel half.== A REAL booking's cancellation still goes out. No regression.

    The short-circuit above must key on ``confirmed_at``, never on ``status``: a confirmed booking
    that is cancelled reads ``cancelled`` too, and its guest is owed the notice.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_to_everything(sqlite_session, tenant)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_11), now=_BEFORE
    )

    await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        now=_BEFORE + timedelta(minutes=5),
        effects=None,
    )

    webhooks = (
        await sqlite_session.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.tenant_id == tenant.id,
                WebhookDelivery.event == "booking.cancelled",
            )
        )
    ).all()
    assert len(list(webhooks)) == 1  # the cancellation WAS announced


@pytest.mark.asyncio
async def test_admin_and_api_create_confirmed_directly_with_a_stamp(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """==Criterion 23b.== Every ``create_booking`` caller gets a CONFIRMED, stamped booking today.

    ``create_booking`` is shared by the public page, the admin and the API key. NOBODY writes
    ``PENDING`` in this wave — holds arrive with payments (B-05b) — so the belt must not
    accidentally starve the host who books a client by hand: every path still produces a real,
    speakable booking. This pins that invariant so a later change cannot quietly regress it.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    assert booking.status is BookingStatus.CONFIRMED
    assert booking.confirmed_at is not None


# --------------------------------------------------------------------------------------
# Defence in depth at the point of EXECUTION — criterion 20b's drainer clause.
# --------------------------------------------------------------------------------------


class _RecordingSender:
    """An in-memory EmailSender that records what it was asked to send (no network)."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_the_drainer_re_verifies_and_will_not_send_for_an_unconfirmed_booking(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """==Criterion 20b, the drainer clause.== A hold's intent that somehow got queued dies at SEND.

    The funnel refuses to queue this — so to test the LAST line of defence we insert the ``Outbox``
    row by hand, exactly modelling *a future enqueue path nobody foresaw* that slipped past the
    funnel. `_is_chain_current` (the existing drain-time guard) only knows ``CANCELLED``, so a
    ``PENDING`` intent passes it and would be sent. The re-verification of ``confirmed_at`` is what
    kills it here instead of at a provider.

    The proof is that the recording sender is never called: the intent is dropped in the READ phase,
    before any message is composed or dispatched.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    hold = await _hold(sqlite_session, tenant, event_type)
    # Straight to the table — NOT through the funnel, which would refuse it. This is the smuggled
    # intent the drainer must catch.
    sqlite_session.add(
        Outbox(
            tenant_id=tenant.id,
            booking_id=hold.id,
            effect=OutboxEffect.EMAIL.value,
            dedupe_key=email_dedupe_key(NotificationKind.CONFIRMATION),
            payload={"kind": "confirmation", "sequence": 0},
            status="pending",
            attempts=0,
        )
    )
    await sqlite_session.commit()

    sender = _RecordingSender()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=sender, service_factory=None
    )
    await drain_outbox(_pools(sqlite_maker), now=_BEFORE, execute=execute)

    # THE proof of silence: the sender was never reached. The message was dropped in the READ phase,
    # before anything could be composed or dispatched.
    assert not sender.sent
    # And it settled as a terminal no-op (exactly like a stale skip), so it is not retried in a loop
    # on every drain — the smuggled intent is put down, not left knocking.
    async with sqlite_maker() as check:
        row = (await check.scalars(select(Outbox).where(Outbox.booking_id == hold.id))).one()
        assert row.status != "pending"


def test_every_effect_declares_whether_it_needs_a_confirmation() -> None:
    """==Criterion 20c's foundation.== The confirmation contract is EXHAUSTIVE over the enum.

    The third such table in this module (staleness and voidability are the others), and for the same
    reason: an effect that inherits a default is an effect nobody decided about. ``REFUND`` and
    ``EXPIRE_HOLD`` (B-05b) will be ``EXEMPT`` — they act on unpaid and cancelled bookings BY
    DEFINITION, and a belt that silenced them would be a belt that keeps the guest's money.

    So a new effect must not COMPILE without choosing. This test is the runtime half of that
    (``assert_never`` is the type-check half): it walks the real enum, so an effect added without a
    branch fails here even if pyright were never run.
    """
    for effect in OutboxEffect:
        assert confirmation_policy(effect) in tuple(Confirmation)

    # Today every effect speaks ABOUT an appointment, so every one of them needs it to be real.
    assert confirmation_policy(OutboxEffect.EMAIL) is Confirmation.REQUIRES_CONFIRMATION
    assert confirmation_policy(OutboxEffect.GOOGLE) is Confirmation.REQUIRES_CONFIRMATION
    assert confirmation_policy(OutboxEffect.NOTIFY) is Confirmation.REQUIRES_CONFIRMATION


# --------------------------------------------------------------------------------------
# ``confirmed_at`` — the switch the whole belt reads.
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_created_booking_is_stamped_confirmed_at(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A booking that IS confirmed carries the instant it became so. Today that is creation time.

    This is the switch every gate below reads. ``status`` cannot be that switch: a cancelled booking
    was confirmed once (its cancellation notice must go out), and a cancelled HOLD never was (its
    must not) — and both rows read ``cancelled``. Only a stamp of the FIRST confirmation tells those
    two apart, which is exactly the question every outbound has to answer.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
    )

    assert booking.status is BookingStatus.CONFIRMED
    assert _stamp(booking) == _BEFORE


@pytest.mark.asyncio
async def test_a_reschedule_successor_inherits_the_original_confirmation(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """The successor of a confirmed booking is confirmed — it INHERITS the stamp, never re-mints it.

    ``reschedule_booking`` does not mutate a booking: it opens a NEW row and cancels the old one. A
    successor left unstamped would make moving a confirmed appointment **silent** — no reschedule
    email, no calendar move, no webhook — with nothing raised anywhere. And the stamp is inherited
    rather than re-taken because it records when this appointment was FIRST confirmed, which a
    reschedule does not change (B-05b hangs the payment on that same chain).
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    later = _BEFORE + timedelta(minutes=5)
    successor = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=later,
    )

    assert successor.id != original.id
    assert successor.status is BookingStatus.CONFIRMED
    assert _stamp(successor) == _BEFORE  # the ORIGINAL confirmation...
    assert _stamp(successor) != later  # ...inherited, not re-minted at the reschedule


@pytest.mark.asyncio
async def test_cancelling_a_confirmed_booking_keeps_its_confirmation_stamp(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A cancelled booking REMEMBERS it was once confirmed — that is what lets its notice go out.

    The stamp is write-once and never cleared. Clearing it on cancellation would silence the
    cancellation email itself: the guest would be told nothing about the appointment they had just
    called off.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    cancelled = await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        now=_BEFORE + timedelta(minutes=5),
    )

    assert cancelled.status is BookingStatus.CANCELLED
    assert _stamp(cancelled) == _BEFORE
