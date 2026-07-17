"""Two concurrent charges on ONE checkout reference must still refund the loser (PostgreSQL).

``db``-marked, and for the reason the other concurrency suites are: the guarantee lives in
server-side concurrency control, which SQLite cannot exercise — it serialises writers anyway.

.. rubric:: The race this closes (Crisol r4)

``apply_paid_event`` resolves a payment by ``provider_ref`` and, failing that, by the
``checkout_session_id`` the row was created with — then BACKFILLS the intent:

.. code-block:: python

    if payment.provider_ref is None:
        payment.provider_ref = provider_ref          # <- read, then write
    elif resolved_by_session and payment.provider_ref != provider_ref:
        return await _refund_second_charge(...)      # <- the double-charge branch

==That is a read-then-write, and the double-charge branch is downstream of it.== Sequentially the
first charge backfills and commits, so the second arrives, sees a DIFFERENT ``provider_ref``, and
is refunded. Concurrently both can read ``provider_ref IS NULL`` before either commits — so BOTH
take the backfill branch, NEITHER sees the other as different, and the second charge is never
recognised as a second charge at all. It then falls through to the conditional UPDATE, finds the
booking already confirmed by *this very row's* payment id, and is read as an idempotent REPLAY.
==We keep a guest's money and log nothing.==

.. rubric:: Why this is Mercado Pago's shape and not Stripe's

A Stripe Checkout Session mints exactly one PaymentIntent, so two charges mean two session ids and
two rows — the race has no window. Mercado Pago's payment carries no preference id, so the anchor is
an ``external_reference`` we choose, and two preferences paid for one booking both echo it onto the
SAME row. The window is real for Mercado Pago, and only for Mercado Pago.

.. rubric:: The hold is what makes this a proof

Without it the two coroutines interleave by luck: the first can commit before the second reads, and
then the second simply takes the ``elif`` and refunds — green having never raced. The event + the
hold pin the first writer's backfill into the database UNCOMMITTED, so the second writer's read
genuinely sees ``NULL`` — the window the bug lives in, made deterministic instead of left to
scheduling luck.

Every reference here is synthetic; ``mp_test_*`` is not a redaction of anything.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Payment,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import ArbiterOutcome, apply_paid_event

pytestmark = pytest.mark.db

_PRICE = 5000
_CUR = "usd"
_PROVIDER = "mercado_pago"
_REFERENCE = "mp_test_external_reference_x"
_CHARGE_ONE = "mp_test_charge_1"
_CHARGE_TWO = "mp_test_charge_2"
_HOLD_SECONDS = 0.5


async def _noop_effects(session: AsyncSession, booking: Booking, now: datetime) -> None:
    """The confirmation chain is not what is under test here."""


async def _seed(owner_maker: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    """A PENDING hold with ONE intent payment anchored on the checkout reference, intent NULL."""
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Double")
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
        now = datetime.now(UTC)
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=30),
            status=BookingStatus.PENDING,
            confirmed_at=None,
            hold_expires_at=now + timedelta(minutes=33),
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        session.add(
            Payment(
                tenant_id=tenant.id,
                booking_id=booking.id,
                provider=_PROVIDER,
                provider_ref=None,
                checkout_session_id=_REFERENCE,
                status=PaymentStatus.INTENT,
                amount_cents=_PRICE,
                currency=_CUR,
            )
        )
        await session.flush()
        return tenant.id, booking.id


async def _apply(session: AsyncSession, tenant_id: uuid.UUID, provider_ref: str):
    return await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider=_PROVIDER,
        provider_ref=provider_ref,
        amount_cents=_PRICE,
        currency=_CUR,
        now=datetime.now(UTC),
        confirm_effects=_noop_effects,
        checkout_session_id=_REFERENCE,
    )


async def test_two_concurrent_charges_on_one_reference_refund_the_loser(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==The race, reproduced and closed.== Both charges read ``provider_ref IS NULL``.

    One must WIN the reference and confirm the booking; the other must be recognised as a SECOND
    charge and refunded. The failure this guards is not an exception — it is silence: the loser
    returning ``REPLAY_NOOP``, no refund row, and a guest's money kept.
    """
    tenant_id, booking_id = await _seed(owner_maker)
    first_has_backfilled = asyncio.Event()

    async def charge_one() -> ArbiterOutcome:
        """Take the backfill, then HOLD it uncommitted so the second charge's read sees NULL."""
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            result = await _apply(session, tenant_id, _CHARGE_ONE)
            first_has_backfilled.set()
            await asyncio.sleep(_HOLD_SECONDS)
            await session.commit()
            return result.outcome

    async def charge_two() -> ArbiterOutcome:
        """Arrive INSIDE the window: the first writer's backfill is written but not committed."""
        await first_has_backfilled.wait()
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            result = await _apply(session, tenant_id, _CHARGE_TWO)
            await session.commit()
            return result.outcome

    outcomes = await asyncio.gather(charge_one(), charge_two(), return_exceptions=True)

    assert not any(isinstance(o, BaseException) for o in outcomes), f"a charge raised: {outcomes!r}"
    assert ArbiterOutcome.CONFIRMED in outcomes, f"nobody confirmed the booking: {outcomes!r}"
    assert ArbiterOutcome.REFUNDED_DOUBLE in outcomes, (
        f"the second charge was not recognised as a second charge: {outcomes!r} — a guest paid "
        "twice and the loser was swallowed as a replay"
    )

    async with owner_maker() as session:
        refunds = (
            await session.scalars(
                sa.select(Outbox).where(
                    Outbox.booking_id == booking_id,
                    Outbox.effect == OutboxEffect.REFUND.value,
                )
            )
        ).all()
        payments = (
            await session.scalars(
                sa.select(Payment)
                .where(Payment.booking_id == booking_id)
                .order_by(Payment.provider_ref)
            )
        ).all()
        booking = await session.get(Booking, booking_id)

    # ==Exactly ONE refund, and it is for the charge that LOST== — never for the one that confirmed.
    assert len(refunds) == 1, f"expected one refund, got {[r.payload for r in refunds]}"
    refunded_ref = refunds[0].payload["provider_ref"]
    assert booking is not None
    assert booking.status is BookingStatus.CONFIRMED, "the winning charge must keep its booking"

    winner = next(p for p in payments if p.id == booking.confirmed_by_payment_id)
    assert refunded_ref != winner.provider_ref, "we refunded the charge that paid for the booking"
    assert {p.provider_ref for p in payments} == {_CHARGE_ONE, _CHARGE_TWO}, (
        f"both charges must be on the ledger; got {[p.provider_ref for p in payments]}"
    )
