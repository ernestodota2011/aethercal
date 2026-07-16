"""Two concurrent resume-checkout writes of one hold must leave ONE payment (PostgreSQL only).

``db``-marked, and for the same reason as ``test_tenant_credentials_concurrency.py``: the guarantee
lives in server-side concurrency control, which SQLite cannot exercise — it serialises writers
anyway.

.. rubric:: The check-then-insert this closes (re-Crisol r4 finding 1)

The resume endpoint opens a checkout — by the booking-id Idempotency-Key the provider returns the
SAME session — and then records the INTENT payment. Two concurrent ``POST .../checkout`` of one hold
both read "no payment yet" and both take the INSERT path with the SAME ``checkout_session_id``; the
``UNIQUE(tenant, provider, checkout_session_id)`` then refuses the second, and "the checkout looked
like it opened and then threw ``IntegrityError``" is exactly the ambiguity the money path must not
have. :func:`record_checkout_intent` closes it with the SAVEPOINT + re-read pattern the repo uses.

.. rubric:: The hold is what makes this a proof

Without it the two coroutines interleave by luck: the first can INSERT and COMMIT before the second
reads, and then the second simply finds the row and returns it — green having never raced. The event
+ the hold pin the first writer's row into the database UNCOMMITTED, so the second writer's read
genuinely misses it and its INSERT genuinely contends for the unique key — the window the bug lives
in, made deterministic instead of left to scheduling luck.

Every secret here is synthetic; ``cs_test_NOT_A_REAL_KEY_*`` is not a redaction of anything.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import Booking, EventType, Payment, Schedule, Tenant, User
from aethercal.server.services.payments import record_checkout_intent

pytestmark = pytest.mark.db

_PRICE = 5000
_CUR = "usd"
_SESSION = "cs_test_NOT_A_REAL_KEY_resume"

# How long the first writer holds its uncommitted row — long enough for the second to wake, bind,
# read (a miss) and reach its blocking INSERT (a couple of localhost round trips), so the contention
# is real rather than a scheduling accident.
_HOLD_SECONDS = 0.5


async def _seed(owner_maker: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    """A PENDING hold on a paid event type, with NO payment yet. ==On the OWNER engine.=="""
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Resume")
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
        return tenant.id, booking.id


async def test_two_concurrent_resumes_collapse_to_one_payment_and_raise_no_integrity_error(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==The TOCTOU, reproduced and closed.== Two resumes, one hold, no payment yet.

    Both open the SAME checkout session (the booking-id Idempotency-Key) and both go to INSERT it;
    the database lets exactly one land. Neither caller may see a raw ``IntegrityError`` — the loser
    absorbs the conflict and returns the row the winner wrote — and the booking must be left with
    ONE payment.
    """
    tenant_id, booking_id = await _seed(owner_maker)
    first_row_pending = asyncio.Event()

    async def writer_one() -> uuid.UUID:
        """Record the INTENT payment, then HOLD it uncommitted so the second writer's read misses it
        and its INSERT has to contend for the unique key."""
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            payment = await record_checkout_intent(
                session,
                tenant_id=tenant_id,
                booking_id=booking_id,
                provider="stripe",
                checkout_session_id=_SESSION,
                amount_cents=_PRICE,
                currency=_CUR,
            )
            payment_id = payment.id
            first_row_pending.set()
            await asyncio.sleep(_HOLD_SECONDS)
            await session.commit()
            return payment_id

    async def writer_two() -> uuid.UUID:
        """Record the SAME checkout session while the first writer's row is uncommitted — its read
        misses, its INSERT contends, and it must resolve to the winner's row, not raise."""
        await first_row_pending.wait()
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            payment = await record_checkout_intent(
                session,
                tenant_id=tenant_id,
                booking_id=booking_id,
                provider="stripe",
                checkout_session_id=_SESSION,
                amount_cents=_PRICE,
                currency=_CUR,
            )
            payment_id = payment.id
            await session.commit()
            return payment_id

    results = await asyncio.gather(writer_one(), writer_two(), return_exceptions=True)

    # ==The loser absorbs the conflict; it does not hand the caller a database traceback.==
    assert not any(isinstance(outcome, IntegrityError) for outcome in results), (
        f"a concurrent resume leaked a raw IntegrityError to the caller: {results!r}"
    )
    assert not any(isinstance(outcome, BaseException) for outcome in results), (
        f"a concurrent resume raised: {results!r}"
    )
    # Both writers resolved to the SAME payment row.
    assert results[0] == results[1], f"the two resumes disagree on the payment row: {results!r}"

    # ONE row — observed on the OWNER engine, which bypasses RLS so a second row could not hide.
    async with owner_maker() as session:
        rows = (
            await session.scalars(sa.select(Payment).where(Payment.booking_id == booking_id))
        ).all()
    assert len(list(rows)) == 1, (
        f"a concurrent resume pair left {len(list(rows))} payments, not one"
    )
