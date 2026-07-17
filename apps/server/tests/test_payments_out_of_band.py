"""Out-of-band refund and dispute events (B-05b, criteria 35 + 36).

Two provider events the arbiter did not cause:

* ``charge.refunded`` we did NOT emit (the operator refunded from the provider's dashboard). Leaving
  the booking confirmed would mean money returned AND the service on the books — so the arbiter
  CANCELS the booking (freeing the slot) and alerts. Its own refund echoed back (``payment`` already
  ``refunded``) is a clean no-op — it does not cancel twice.
* ``charge.dispute.created``. A dispute is not a resolution: the arbiter MARKS and alerts, and does
  NOT cancel (a dispute later won would be worse than the problem).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    OutboxStatus,
    Payment,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.bookings import (
    BookingEffects,
    cancel_confirmed_booking_effects,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import (
    ArbiterOutcome,
    CancelEffects,
    apply_dispute_event,
    apply_refunded_event,
    enqueue_refund,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"
_REF_B = "pi_test_NOT_A_REAL_KEY_B"


def _cancel_effects() -> CancelEffects:
    """The FULL cancellation chain (r5 finding 2), the SAME one the webhook layer injects in prod —
    so the arbiter's out-of-band path runs the real ``cancel_confirmed_booking_effects``, not a
    partial copy. The ``effects`` bundle is present, so Google DELETE + email are enqueued."""
    effects = BookingEffects(
        signer=GuestTokenSigner("test-secret"), booking_base_url="https://book.example.com"
    )

    async def _run(session: AsyncSession, booking: Booking, now: datetime) -> None:
        await cancel_confirmed_booking_effects(session, booking=booking, effects=effects, now=now)

    return _run


async def _seed(
    session: AsyncSession,
    *,
    payment_status: PaymentStatus = PaymentStatus.PAID,
    external_event_id: str | None = None,
) -> tuple[uuid.UUID, Booking, Payment]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
    session.add_all([host, schedule])
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="paid",
        title="Paid",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 60,
        price_cents=_PRICE,
        currency=_CUR,
    )
    session.add(event_type)
    await session.flush()
    payment = Payment(
        tenant_id=tenant.id,
        booking_id=uuid.uuid4(),
        provider="stripe",
        provider_ref=_REF,
        status=payment_status,
        amount_cents=_PRICE,
        currency=_CUR,
    )
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_SLOT,
        end_at=_SLOT + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        confirmed_at=NOW,
        confirmed_by_payment_id=payment.id,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
        # A booking already synced to the host's Google Calendar — so the cancellation must DELETE
        # that event (the chain-has-external-event gate in ``_enqueue_google``).
        external_event_id=external_event_id,
    )
    session.add(booking)
    await session.flush()
    payment.booking_id = booking.id
    session.add(payment)
    await session.flush()
    return tenant.id, booking, payment


async def _count(session: AsyncSession, booking_id: uuid.UUID, effect: OutboxEffect) -> int:
    return len(
        list(
            (
                await session.scalars(
                    select(Outbox).where(
                        Outbox.booking_id == booking_id,
                        Outbox.effect == effect.value,
                    )
                )
            ).all()
        )
    )


async def _apply_refund(session: AsyncSession, tenant_id: uuid.UUID) -> ArbiterOutcome:
    result = await apply_refunded_event(
        session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF,
        now=NOW,
        cancel_effects=_cancel_effects(),
    )
    return result.outcome


async def test_an_out_of_band_refund_cancels_the_booking_and_frees_the_slot(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 35.== A refund we did not emit (payment still ``paid``) cancels the booking, the
    slot is freed — never money returned AND the service delivered. No SECOND refund is queued (the
    money already went back), and the guest is told (a cancellation email)."""
    tenant_id, booking, payment = await _seed(sqlite_session)

    outcome = await _apply_refund(sqlite_session, tenant_id)

    assert outcome is ArbiterOutcome.OUT_OF_BAND_REFUND
    await sqlite_session.refresh(booking)
    await sqlite_session.refresh(payment)
    assert booking.status is BookingStatus.CANCELLED, "the slot is freed"
    assert payment.status is PaymentStatus.REFUNDED
    assert await _count(sqlite_session, booking.id, OutboxEffect.REFUND) == 0, "money already back"
    assert await _count(sqlite_session, booking.id, OutboxEffect.EMAIL) == 1, "the guest is told"


async def test_an_out_of_band_refund_runs_the_full_cancellation_chain(
    sqlite_session: AsyncSession,
) -> None:
    """==Re-Crisol r5 finding 2.== A CONFIRMED booking already synced to Google, cancelled by an
    external refund, must run the FULL cancellation chain — not a partial copy. It enqueues the
    guest cancellation email AND the Google-Calendar DELETE (so the event does not linger in the
    host's calendar), exactly as ``cancel_booking``. Before this fix only the email/void ran."""
    tenant_id, booking, _payment = await _seed(sqlite_session, external_event_id="google-event-abc")

    outcome = await _apply_refund(sqlite_session, tenant_id)

    assert outcome is ArbiterOutcome.OUT_OF_BAND_REFUND
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED
    assert await _count(sqlite_session, booking.id, OutboxEffect.EMAIL) == 1, "the guest is told"
    # ==The gap this fix closes.== The Google event is deleted, not left in the host's calendar.
    assert await _count(sqlite_session, booking.id, OutboxEffect.GOOGLE) == 1, (
        "delete the GCal event"
    )
    assert await _count(sqlite_session, booking.id, OutboxEffect.REFUND) == 0, "money already back"


async def test_our_own_refund_echoed_back_is_a_noop(sqlite_session: AsyncSession) -> None:
    """Our OWN refund makes the provider send ``charge.refunded`` back. The payment is already
    ``refunded``, so the arbiter must NOT cancel the (already-cancelled) booking a second time."""
    tenant_id, booking, _payment = await _seed(
        sqlite_session, payment_status=PaymentStatus.REFUNDED
    )

    outcome = await _apply_refund(sqlite_session, tenant_id)

    assert outcome is ArbiterOutcome.REFUND_ECHO
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED, "our own refund echo does not re-cancel"
    assert await _count(sqlite_session, booking.id, OutboxEffect.EMAIL) == 0


async def test_an_out_of_band_refund_voids_the_pending_reminders(
    sqlite_session: AsyncSession,
) -> None:
    """==Re-Crisol #3.== An external refund cancels the booking AND reconciles its queued effects.
    Setting the status is not enough: a ``before_start`` reminder still sitting in the outbox would
    fire the hour before a meeting that was refunded and cancelled. The out-of-band path runs the
    same ``CANCEL`` transition ``cancel_booking`` does, which VOIDS those pending NOTIFY steps."""
    tenant_id, booking, _payment = await _seed(sqlite_session)
    reminder = Outbox(
        tenant_id=tenant_id,
        booking_id=booking.id,
        effect=OutboxEffect.NOTIFY.value,
        dedupe_key=f"wf:{uuid.uuid4()}:step:whatsapp",
        payload={
            "trigger": "before_start",
            "channel": "whatsapp",
            "kind": "reminder",
            "step_id": str(uuid.uuid4()),
        },
        status=OutboxStatus.PENDING.value,
        attempts=0,
        next_retry_at=_SLOT - timedelta(days=1),  # a real queued reminder, due 24 h before the slot
    )
    sqlite_session.add(reminder)
    await sqlite_session.flush()
    reminder_id = reminder.id

    result = await _apply_refund(sqlite_session, tenant_id)

    assert result is ArbiterOutcome.OUT_OF_BAND_REFUND
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED
    voided = await sqlite_session.get(Outbox, reminder_id)
    assert voided is not None
    assert voided.status == OutboxStatus.VOIDED.value, (
        "the pending reminder must be voided, not fire after the external refund cancelled it"
    )


# --------------------------------------------------------------------------------------
# The echo that has not committed yet (re-Crisol r8). ==payment.status is written AFTER the
# provider call, so it cannot discriminate OUR refund from the operator's inside that window.==
# --------------------------------------------------------------------------------------


async def _add_double_payment(
    session: AsyncSession, *, tenant_id: uuid.UUID, booking: Booking
) -> Payment:
    """A SECOND charge on the one booking — the double payment the arbiter refunds (criterion 28).

    ``paid``, and NOT the payment that confirmed the booking: exactly the row
    ``apply_paid_event``'s ``REFUNDED_DOUBLE`` branch leaves behind when it queues the refund of the
    loser's charge while the booking stays CONFIRMED on the winner's.
    """
    payment = Payment(
        tenant_id=tenant_id,
        booking_id=booking.id,
        provider="stripe",
        provider_ref=_REF_B,
        status=PaymentStatus.PAID,
        amount_cents=_PRICE,
        currency=_CUR,
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_our_own_refund_echoed_before_the_runner_commits_never_cancels_a_live_booking(
    sqlite_session: AsyncSession,
) -> None:
    """==Re-Crisol r8.== The guest paid TWICE. The arbiter confirmed on charge A, queued a refund of
    charge B, and the runner called the provider — but the provider's ``charge.refunded`` for B
    lands BEFORE the runner's ``status = refunded`` commits, so this session still reads B as
    ``paid``.

    ``payment.status`` therefore says "not our refund" about a refund that is entirely ours. The
    out-of-band branch then reads the booking — CONFIRMED and live, because it belongs to charge A —
    and the guard on ``status is not CANCELLED`` does NOT hold it back: it tears up a paid
    appointment. Google event deleted, guest emailed a cancellation, slot given away, and charge A's
    money kept. The one outcome this module's own header says it cannot get wrong.

    The discriminator must be a fact committed BEFORE the provider was ever called — the REFUND
    intent in the outbox — not a status written after it returns.
    """
    tenant_id, booking, _payment_a = await _seed(
        sqlite_session, external_event_id="google-event-abc"
    )
    payment_b = await _add_double_payment(sqlite_session, tenant_id=tenant_id, booking=booking)
    # What the arbiter committed BEFORE the runner ever reached the provider (criterion 30's key).
    await enqueue_refund(sqlite_session, booking=booking, provider="stripe", provider_ref=_REF_B)
    await sqlite_session.flush()

    result = await apply_refunded_event(
        sqlite_session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF_B,
        now=NOW,
        cancel_effects=_cancel_effects(),
    )

    assert result.outcome is ArbiterOutcome.REFUND_ECHO
    await sqlite_session.refresh(booking)
    await sqlite_session.refresh(payment_b)
    assert booking.status is BookingStatus.CONFIRMED, "the guest's paid appointment must survive"
    assert booking.cancelled_at is None
    assert await _count(sqlite_session, booking.id, OutboxEffect.EMAIL) == 0, (
        "no cancellation email for an appointment that is still on"
    )
    assert await _count(sqlite_session, booking.id, OutboxEffect.GOOGLE) == 0, (
        "the host's calendar event must NOT be deleted"
    )
    # The echo is still PROOF the money went back: converge the ledger, so the runner's re-check
    # (and any re-drain) is a clean no-op instead of asking the provider to refund a charge that
    # is already refunded.
    assert payment_b.status is PaymentStatus.REFUNDED


async def test_our_own_refund_echo_does_not_raise_the_out_of_band_alert(
    sqlite_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """==Re-Crisol r8.== The ordinary path: the guest cancelled inside the window, so
    ``cancel_booking`` committed the cancellation together with the REFUND intent, and the runner
    refunded. The echo
    racing the runner's commit reads ``paid`` and lands in the out-of-band branch — where the
    CANCELLED guard does spare the effects, but the ALERT still fires.

    An alert that cries "a human must look at this" on EVERY ordinary refund is an alert that gets
    tuned out — and then the real out-of-band refund, the only thing it exists to catch, scrolls
    past unread. Noise that trains the reader to ignore the signal is the same failure as silence.
    """
    tenant_id, booking, _payment = await _seed(sqlite_session)
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = NOW
    await enqueue_refund(sqlite_session, booking=booking, provider="stripe", provider_ref=_REF)
    await sqlite_session.flush()

    with caplog.at_level(logging.ERROR):
        outcome = await _apply_refund(sqlite_session, tenant_id)

    assert outcome is ArbiterOutcome.REFUND_ECHO
    assert "OUT-OF-BAND" not in caplog.text, "our own ordinary refund must not page a human"


async def test_our_refund_echo_is_recognised_after_the_charge_followed_a_reschedule(
    sqlite_session: AsyncSession,
) -> None:
    """==Re-Crisol r8 — why the lookup is NOT keyed on the booking.== A reschedule moves EVERY
    payment to the successor (``_repoint_payments``), a double payment's row included; nothing
    re-points the outbox, so the REFUND intent keeps the booking id it was queued under. The two
    part company here.

    Key the echo lookup on ``payment.booking_id`` and it misses our own intent on exactly this
    path — and the branch it then falls into cancels the live SUCCESSOR the guest was rescheduled
    into. The charge reference in the dedupe key is what identifies the money, so that is what the
    lookup matches on.
    """
    tenant_id, predecessor, _payment_a = await _seed(sqlite_session)
    payment_b = await _add_double_payment(sqlite_session, tenant_id=tenant_id, booking=predecessor)
    # Queued while the charge still belonged to the predecessor.
    await enqueue_refund(
        sqlite_session, booking=predecessor, provider="stripe", provider_ref=_REF_B
    )
    successor = Booking(
        tenant_id=tenant_id,
        event_type_id=predecessor.event_type_id,
        start_at=_SLOT + timedelta(days=1),
        end_at=_SLOT + timedelta(days=1, minutes=30),
        status=BookingStatus.CONFIRMED,
        confirmed_at=NOW,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    sqlite_session.add(successor)
    await sqlite_session.flush()
    # The reschedule: the charge now points at the successor, the intent still at the predecessor.
    payment_b.booking_id = successor.id
    await sqlite_session.flush()

    result = await apply_refunded_event(
        sqlite_session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF_B,
        now=NOW,
        cancel_effects=_cancel_effects(),
    )

    assert result.outcome is ArbiterOutcome.REFUND_ECHO
    await sqlite_session.refresh(successor)
    assert successor.status is BookingStatus.CONFIRMED, "the rescheduled appointment must survive"
    assert await _count(sqlite_session, successor.id, OutboxEffect.EMAIL) == 0


async def test_a_refund_we_never_queued_is_still_out_of_band(sqlite_session: AsyncSession) -> None:
    """==The other direction, and the one that must NOT regress (re-Crisol r8).== Narrowing what
    counts as "our echo" is only safe if a refund we did NOT cause still gets caught. No REFUND
    intent was ever queued for this charge, so the operator refunded it from the provider's
    dashboard: cancel the booking, free the slot, and page a human. Swallowing THIS as an echo
    would be money returned AND the service still delivered, in silence."""
    tenant_id, booking, payment = await _seed(sqlite_session)

    outcome = await _apply_refund(sqlite_session, tenant_id)

    assert outcome is ArbiterOutcome.OUT_OF_BAND_REFUND
    await sqlite_session.refresh(booking)
    await sqlite_session.refresh(payment)
    assert booking.status is BookingStatus.CANCELLED
    assert payment.status is PaymentStatus.REFUNDED


async def test_a_dispute_marks_and_alerts_but_does_not_cancel(sqlite_session: AsyncSession) -> None:
    """==Criterion 36.== A dispute is not a resolution: mark + alert, do NOT cancel — a dispute
    later won would be worse than the problem."""
    tenant_id, booking, payment = await _seed(sqlite_session)

    result = await apply_dispute_event(
        sqlite_session, tenant_id=tenant_id, provider="stripe", provider_ref=_REF, now=NOW
    )

    assert result.outcome is ArbiterOutcome.DISPUTE_MARKED
    await sqlite_session.refresh(booking)
    await sqlite_session.refresh(payment)
    assert booking.status is BookingStatus.CONFIRMED, "a dispute never auto-cancels"
    assert payment.status is PaymentStatus.PAID
    assert await _count(sqlite_session, booking.id, OutboxEffect.REFUND) == 0
