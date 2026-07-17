"""The per-IP cap — the one that was **required at boot and controlled nothing at all**.

``DailyCaps.per_ip`` has always failed the boot when unset, and its own docstring confessed that no
client IP ever reached the send path: ``bookings`` had no column for it. A knob that reads as
protection and enforces nothing is worse than no knob, because everybody downstream believes it is
there. This is where it is closed, and these tests say what "closed" means.

.. rubric:: A column is NOT the fix

Adding ``bookings.source_ip`` and stopping there would leave the cap *looking* applied while still
denying nothing — the same no-op, now with a schema behind it. So the assertions here are about a
**denial**:

* the cap DENIES a send once the address behind it has spent its budget (criterion 17b);
* it counts across **bookings**, so booking ten times from one address does not buy ten budgets;
* it counts **CONFIRMED** bookings — which, since a free event type confirms directly, is the only
  kind that exists today. A cap that only knew about unpaid holds would cover nothing (criterion
  17);
* a booking with **no** address (the admin's own, the API key's own) is NOT capped — the host
  booking a guest by hand must never be throttled by a stranger's traffic.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, EventType, Schedule, SentNotification, Tenant, User
from aethercal.server.integrations.messaging.guard import (
    DailyCaps,
    QuotaExceeded,
    enforce_ip_cap,
    sends_from_ip_in_window,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
_DAY = timedelta(days=1)

_IP = "203.0.113.9"
_OTHER_IP = "198.51.100.4"
_CAPS = DailyCaps(per_phone=100, per_ip=2)


async def _booking(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    tenant: Tenant | None = None,
    source_ip: str | None = _IP,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
    resolved = tenant or await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == resolved.id))).first()
    assert host is not None
    unique = uuid.uuid4().hex[:8]
    schedule = Schedule(tenant_id=resolved.id, name=f"S-{unique}", timezone="UTC", rules={})
    session.add(schedule)
    await session.flush()
    event_type = EventType(
        tenant_id=resolved.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug=f"e-{unique}",
        title="Consulta",
        duration_seconds=1800,
        max_advance_seconds=2_592_000,
    )
    session.add(event_type)
    await session.flush()
    booking = Booking(
        tenant_id=resolved.id,
        event_type_id=event_type.id,
        start_at=_START,
        end_at=_START + timedelta(minutes=30),
        status=status,
        guest_name="Ada",
        guest_email=f"ada+{unique}@example.com",
        guest_phone="+13055550123",
        guest_phone_consent_at=NOW - timedelta(days=2),
        guest_timezone="UTC",
        source_ip=source_ip,
    )
    session.add(booking)
    await session.flush()
    return booking


async def _record_send(
    session: AsyncSession,
    booking: Booking,
    *,
    channel: Channel,
    sent_at: datetime | None = None,
) -> None:
    session.add(
        SentNotification(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            kind="reminder",
            channel=channel.value,
            sent_at=sent_at if sent_at is not None else NOW - timedelta(hours=1),
        )
    )
    await session.flush()


async def _count(session: AsyncSession, *, tenant: Tenant, source_ip: str = _IP) -> int:
    return await sends_from_ip_in_window(
        session,
        tenant_id=tenant.id,
        source_ip=source_ip,
        channel=Channel.WHATSAPP,
        since=NOW - _DAY,
    )


# --------------------------------------------------------------------------------------
# The count: the EFFECTIVE state (the ledger), joined to the address on the booking.
# --------------------------------------------------------------------------------------


async def test_the_count_is_of_messages_really_sent_from_that_address(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, booking, channel=Channel.WHATSAPP)

    assert await _count(sqlite_session, tenant=tenant) == 1


async def test_it_counts_ACROSS_bookings_so_booking_again_buys_no_new_budget(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==The attack this is for.== The cheap way to defeat a per-booking ceiling is to make more
    bookings; the address is what an attacker cannot mint on demand, so the address is the key."""
    tenant = await tenant_factory(sqlite_session)
    first = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    second = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, first, channel=Channel.WHATSAPP)
    await _record_send(sqlite_session, second, channel=Channel.WHATSAPP)

    assert await _count(sqlite_session, tenant=tenant) == 2


async def test_a_CONFIRMED_booking_counts_towards_the_cap(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Criterion 17. A free event type confirms DIRECTLY — there is no hold to count — so a cap that
    only looked at unpaid holds would cover exactly nothing. There is no status filter in the count,
    and this test exists to fail the day somebody adds one."""
    tenant = await tenant_factory(sqlite_session)
    booking = await _booking(
        sqlite_session, tenant_factory, tenant=tenant, status=BookingStatus.CONFIRMED
    )
    await _record_send(sqlite_session, booking, channel=Channel.WHATSAPP)

    assert booking.status is BookingStatus.CONFIRMED
    assert await _count(sqlite_session, tenant=tenant) == 1


async def test_another_address_spends_its_own_budget(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    theirs = await _booking(sqlite_session, tenant_factory, tenant=tenant, source_ip=_OTHER_IP)
    await _record_send(sqlite_session, theirs, channel=Channel.WHATSAPP)

    assert await _count(sqlite_session, tenant=tenant) == 0


async def test_a_neighbours_traffic_never_spends_this_business_budget(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    mine = await tenant_factory(sqlite_session)
    theirs = await _booking(sqlite_session, tenant_factory)  # a DIFFERENT business, same address
    await _record_send(sqlite_session, theirs, channel=Channel.WHATSAPP)

    assert await _count(sqlite_session, tenant=mine) == 0


async def test_each_channel_has_its_own_bill_and_its_own_budget(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(sqlite_session)
    booking = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, booking, channel=Channel.SMS)

    assert await _count(sqlite_session, tenant=tenant) == 0


# --------------------------------------------------------------------------------------
# The DENIAL — criterion 17b. Not "the column exists": the send is refused.
# --------------------------------------------------------------------------------------


async def test_under_the_cap_the_send_is_allowed(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Asserted first, or every refusal below passes for free: a guard that refused EVERYTHING would
    satisfy them all — and would also have broken the product."""
    tenant = await tenant_factory(sqlite_session)
    first = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    second = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, first, channel=Channel.WHATSAPP)

    await enforce_ip_cap(
        sqlite_session, booking=second, channel=Channel.WHATSAPP, caps=_CAPS, now=NOW
    )


async def test_at_the_cap_the_send_is_DENIED(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==Criterion 17b.== The address has spent its budget, and the next message does not go."""
    tenant = await tenant_factory(sqlite_session)
    first = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    second = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    third = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, first, channel=Channel.WHATSAPP)
    await _record_send(sqlite_session, second, channel=Channel.WHATSAPP)

    with pytest.raises(QuotaExceeded, match="per-ip cap"):
        await enforce_ip_cap(
            sqlite_session, booking=third, channel=Channel.WHATSAPP, caps=_CAPS, now=NOW
        )


async def test_a_booking_with_NO_address_is_not_capped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The host booking a guest from the admin, and the tenant's own API key. Neither came through
    the public form, so neither has an address — and neither may be throttled by one.

    ==Deliberately the OPPOSITE of the per-phone rule==, where an absent number REFUSES the send.
    There, the missing value IS the thing being messaged: without it there is nothing to send to.
    Here, the missing value means the booking never came from the public form at all, and refusing
    it
    would silence the host's own appointments."""
    tenant = await tenant_factory(sqlite_session)
    spent_first = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    spent_second = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(sqlite_session, spent_first, channel=Channel.WHATSAPP)
    await _record_send(sqlite_session, spent_second, channel=Channel.WHATSAPP)
    by_the_host = await _booking(sqlite_session, tenant_factory, tenant=tenant, source_ip=None)

    await enforce_ip_cap(
        sqlite_session, booking=by_the_host, channel=Channel.WHATSAPP, caps=_CAPS, now=NOW
    )


async def test_yesterdays_traffic_does_not_hold_todays_guest_hostage(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The window ROLLS. A send outside it is spent budget that has been given back."""
    tenant = await tenant_factory(sqlite_session)
    old = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    fresh = await _booking(sqlite_session, tenant_factory, tenant=tenant)
    await _record_send(
        sqlite_session, old, channel=Channel.WHATSAPP, sent_at=NOW - timedelta(days=3)
    )

    assert await _count(sqlite_session, tenant=tenant) == 0
    await enforce_ip_cap(
        sqlite_session, booking=fresh, channel=Channel.WHATSAPP, caps=_CAPS, now=NOW
    )
