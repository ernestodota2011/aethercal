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

import uuid
from datetime import UTC, datetime, timedelta

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
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"


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
