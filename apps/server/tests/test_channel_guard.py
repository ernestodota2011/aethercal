"""The phone-channel guard (RF-24): caps are FAIL-CLOSED, and the cap counts what was really sent.

The recipient of a WhatsApp/SMS step is untrusted input — it comes from the **public booking form**,
so anyone can book with a stranger's number and make this system message them. The messaging account
we send from may be one other systems depend on, and a spam complaint against it is not recoverable.

Hence two properties, tested here:

* **fail-closed configuration** — a phone channel that has not been given its daily caps REFUSES to
  exist. Not "defaults to unlimited", not "logs and continues": a cap you forgot to set is exactly
  the misconfiguration whose symptom is *nothing appears wrong until the bill arrives*;
* **the cap counts the EFFECTIVE state** — the messages actually recorded as sent to that phone,
  read back from the ``sent_notifications`` ledger. An in-process counter would be reset to zero by
  every restart and by every second worker, so the cap would "hold" in the tests and mean nothing in
  production.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.models import Booking, EventType, Schedule, SentNotification, Tenant, User
from aethercal.server.integrations.messaging.guard import (
    DailyCaps,
    QuotaExceeded,
    enforce_phone_cap,
    phone_sends_in_window,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
_DAY = timedelta(days=1)

_PHONE = "+13055550123"


async def _seed_booking(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    phone: str | None = _PHONE,
    tenant: Tenant | None = None,
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
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_phone=phone,
        guest_phone_consent_at=NOW - timedelta(days=2),
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return booking


async def _record_send(
    session: AsyncSession,
    booking: Booking,
    *,
    channel: Channel,
    sent_at: datetime,
    kind: str = "reminder",
) -> None:
    """Record one message in the ledger, exactly as the NOTIFY handler does after a real send.

    ``kind`` varies between calls for the SAME booking on purpose: the ledger's partial unique index
    permits only one row per (tenant, booking, kind, channel) while ``step_id`` is NULL, so two
    messages to one booking necessarily differ by kind (or by step) — which is precisely how they
    arise in production, from two different workflow steps."""
    session.add(
        SentNotification(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            kind=kind,
            channel=channel.value,
            sent_at=sent_at,
        )
    )
    await session.flush()


async def _count(session: AsyncSession, booking: Booking, channel: Channel) -> int:
    return await phone_sends_in_window(
        session,
        tenant_id=booking.tenant_id,
        phone=_PHONE,
        channel=channel,
        since=NOW - _DAY,
    )


# --------------------------------------------------------------------------------------
# Fail-closed configuration.
# --------------------------------------------------------------------------------------


def test_caps_must_be_positive() -> None:
    """A zero or negative cap is not "unlimited" — it is a typo, and it must not boot."""
    for per_phone, per_ip in ((0, 5), (5, 0), (-1, 5), (5, -1)):
        with pytest.raises(ValueError, match="cap"):
            DailyCaps(per_phone=per_phone, per_ip=per_ip)


def test_caps_from_env_refuses_to_build_without_both_caps() -> None:
    """The channel refuses to ACTIVATE without its caps. An unconfigured channel is off (fine); a
    HALF-configured one that sends with no ceiling is the failure this exists to prevent."""
    with pytest.raises(RuntimeError, match="DAILY_CAP_PER_PHONE"):
        DailyCaps.from_env({"AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "50"}, prefix="WHATSAPP")

    with pytest.raises(RuntimeError, match="DAILY_CAP_PER_IP"):
        DailyCaps.from_env({"AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "5"}, prefix="WHATSAPP")


def test_caps_from_env_rejects_a_non_integer() -> None:
    with pytest.raises(RuntimeError, match="integer"):
        DailyCaps.from_env(
            {
                "AETHERCAL_SMS_DAILY_CAP_PER_PHONE": "lots",
                "AETHERCAL_SMS_DAILY_CAP_PER_IP": "50",
            },
            prefix="SMS",
        )


def test_caps_from_env_reads_both_caps() -> None:
    caps = DailyCaps.from_env(
        {"AETHERCAL_SMS_DAILY_CAP_PER_PHONE": "3", "AETHERCAL_SMS_DAILY_CAP_PER_IP": "40"},
        prefix="SMS",
    )

    assert caps == DailyCaps(per_phone=3, per_ip=40)


# --------------------------------------------------------------------------------------
# The cap counts what was REALLY sent — the ledger, not a counter in this process's memory.
# --------------------------------------------------------------------------------------


async def test_the_window_counts_sends_recorded_in_the_ledger(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    for hours, kind in ((1, "reminder"), (2, "confirmation")):
        await _record_send(
            sqlite_session,
            booking,
            channel=Channel.WHATSAPP,
            sent_at=NOW - timedelta(hours=hours),
            kind=kind,
        )

    assert await _count(sqlite_session, booking, Channel.WHATSAPP) == 2


async def test_the_window_counts_across_bookings_for_the_same_phone(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The cap protects a PERSON, not a booking. An attacker who books ten times with a stranger's
    number sails straight past a per-booking limit while the stranger's phone rings ten times."""
    tenant = await tenant_factory(sqlite_session)
    first = await _seed_booking(sqlite_session, tenant_factory, tenant=tenant)
    second = await _seed_booking(sqlite_session, tenant_factory, tenant=tenant)
    for booking in (first, second):
        await _record_send(
            sqlite_session, booking, channel=Channel.WHATSAPP, sent_at=NOW - timedelta(hours=1)
        )

    assert await _count(sqlite_session, first, Channel.WHATSAPP) == 2


async def test_the_window_ignores_older_sends(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    await _record_send(
        sqlite_session, booking, channel=Channel.WHATSAPP, sent_at=NOW - timedelta(days=2)
    )

    assert await _count(sqlite_session, booking, Channel.WHATSAPP) == 0


async def test_the_window_is_scoped_per_channel(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """Each channel has its own account, its own bill and its own reputation to lose."""
    booking = await _seed_booking(sqlite_session, tenant_factory)
    for channel in (Channel.SMS, Channel.EMAIL):
        await _record_send(
            sqlite_session, booking, channel=channel, sent_at=NOW - timedelta(hours=1)
        )

    assert await _count(sqlite_session, booking, Channel.WHATSAPP) == 0


async def test_the_window_is_scoped_per_tenant(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """One business must never spend another business's budget — nor be throttled by it."""
    other = await tenant_factory(sqlite_session, slug="other", email="other@example.com")
    theirs = await _seed_booking(sqlite_session, tenant_factory, tenant=other)
    await _record_send(
        sqlite_session, theirs, channel=Channel.WHATSAPP, sent_at=NOW - timedelta(hours=1)
    )
    mine = await _seed_booking(sqlite_session, tenant_factory)

    assert await _count(sqlite_session, mine, Channel.WHATSAPP) == 0


# --------------------------------------------------------------------------------------
# Enforcement.
# --------------------------------------------------------------------------------------


async def test_the_cap_allows_a_send_below_the_ceiling(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    await _record_send(
        sqlite_session, booking, channel=Channel.WHATSAPP, sent_at=NOW - timedelta(hours=1)
    )

    await enforce_phone_cap(
        sqlite_session,
        booking=booking,
        channel=Channel.WHATSAPP,
        caps=DailyCaps(per_phone=2, per_ip=50),
        now=NOW,
    )


async def test_the_cap_refuses_the_send_at_the_ceiling(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    booking = await _seed_booking(sqlite_session, tenant_factory)
    for hours, kind in ((1, "reminder"), (2, "confirmation")):
        await _record_send(
            sqlite_session,
            booking,
            channel=Channel.WHATSAPP,
            sent_at=NOW - timedelta(hours=hours),
            kind=kind,
        )

    with pytest.raises(QuotaExceeded, match="daily-cap"):
        await enforce_phone_cap(
            sqlite_session,
            booking=booking,
            channel=Channel.WHATSAPP,
            caps=DailyCaps(per_phone=2, per_ip=50),
            now=NOW,
        )


async def test_a_booking_with_no_phone_is_refused_rather_than_uncounted(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """With no phone there is nothing to key the cap on. Letting it through "because the count came
    back zero" is a hole every unbounded send walks straight through."""
    booking = await _seed_booking(sqlite_session, tenant_factory, phone=None)

    with pytest.raises(QuotaExceeded):
        await enforce_phone_cap(
            sqlite_session,
            booking=booking,
            channel=Channel.WHATSAPP,
            caps=DailyCaps(per_phone=2, per_ip=50),
            now=NOW,
        )
