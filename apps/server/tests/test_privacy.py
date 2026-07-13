"""RNF-8 — guest erasure. ==A partial purge is worse than no purge at all.==

Worse, because it *reports success*. The operator answers the erasure request, closes the ticket,
and the guest's name and email are still sitting in three other tables. The next person to find that
out is the regulator.

So these tests do not check that "the purge ran". They check, by name, that the PII is gone from
every place it lives — and two of them are STRUCTURAL, so that the next column and the next table
cannot quietly slip through:

* every ``guest_*`` column on ``bookings`` must be CLASSIFIED (erased, or explicitly retained). Add
  ``guest_date_of_birth`` tomorrow and forget the purge, and the suite fails;
* every table carrying a ``booking_id`` must be HANDLED. The channels cut is about to add rendered
  message bodies hanging off a booking; when it lands, this fails until the purge accounts for it.

And the catch-all: after the purge, the guest's email and name appear NOWHERE in the database — not
in a column, not in a JSON payload, at any depth. That test reads ``Base.metadata`` instead of a
hand-written list of tables, which is the entire point: a test that knows which tables exist cannot
notice the one somebody adds tomorrow.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.cli import run_guest_purge
from aethercal.server.db import Base
from aethercal.server.db.models import (
    Booking,
    EventType,
    GuestToken,
    Outbox,
    OutboxStatus,
    Schedule,
    SentNotification,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.privacy import (
    BOOKING_PII_COLUMNS,
    BOOKING_RETAINED_GUEST_COLUMNS,
    ERASED_EMAIL,
    ERASED_NAME,
    TABLES_PURGED_BY_BOOKING,
    purge_guest,
)

_EMAIL = "ada@example.com"
_NAME = "Ada Lovelace"
_PHONE = "+13055551234"
_NOTES = "Please call me on my mobile, I am Ada Lovelace."
_ANSWER = "A friend told me"
_START = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


async def _tenant(session: AsyncSession, slug: str) -> tuple[Tenant, EventType]:
    tenant = Tenant(slug=slug, name=slug)
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="host@example.com", name="H", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
    session.add_all([host, schedule])
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="intro",
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )
    session.add(event_type)
    await session.flush()
    return tenant, event_type


async def _guest_booking(
    session: AsyncSession,
    tenant: Tenant,
    event_type: EventType,
    *,
    email: str = _EMAIL,
    start: datetime = _START,
) -> Booking:
    """A booking with the guest's PII in every column that can hold it — and its whole trail.

    A guest token, an outbox intent whose payload names them, a sent-notification ledger row, and a
    webhook delivery carrying the entire serialised booking inside a JSON blob.
    """
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        guest_name=_NAME,
        guest_email=email,
        guest_phone=_PHONE,
        guest_phone_consent_at=start,
        guest_timezone="America/New_York",
        guest_notes=_NOTES,
        answers={"how_did_you_hear": _ANSWER, "contact": email},
    )
    session.add(booking)
    await session.flush()

    session.add(
        GuestToken(
            tenant_id=tenant.id,
            booking_id=booking.id,
            purpose="cancel",
            token_hash=f"hash-{uuid.uuid4().hex}",
            expires_at=start + timedelta(days=1),
        )
    )
    session.add(
        Outbox(
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=OutboxEffect.GOOGLE.value,
            dedupe_key="google:upsert",
            # `services/bookings.py` puts the guest's address in here.
            payload={"operation": "upsert", "summary": "Intro", "guest_email": email},
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
    )
    session.add(
        SentNotification(
            tenant_id=tenant.id, booking_id=booking.id, kind="confirmation", channel="email"
        )
    )
    webhook = Webhook(
        tenant_id=tenant.id,
        url="https://consumer.test/hook",
        secret=b"opaque",
        events=["booking.created"],
        active=True,
    )
    session.add(webhook)
    await session.flush()
    session.add(
        WebhookDelivery(
            tenant_id=tenant.id,
            webhook_id=webhook.id,
            event="booking.created",
            payload={
                "event": "booking.created",
                "api_version": "1",
                "timestamp": start.isoformat(),
                "data": {
                    "id": str(booking.id),
                    "guest_name": _NAME,
                    "guest_email": email,
                    "guest_notes": _NOTES,
                    "answers": {"contact": email},
                },
            },
            status="delivered",
            attempts=1,
        )
    )
    await session.flush()
    return booking


async def _everything_in_the_database(session: AsyncSession) -> str:
    """Every row of every table, stringified — the catch-all's haystack.

    Reads ``Base.metadata`` deliberately: a test that hard-codes which tables to look in cannot
    notice the table somebody adds tomorrow, which is the exact failure RNF-8 is about."""
    chunks: list[str] = []
    for table in Base.metadata.sorted_tables:
        for row in (await session.execute(sa.select(table))).mappings():
            chunks.append(json.dumps({key: str(value) for key, value in row.items()}))
    return "\n".join(chunks)


# --------------------------------------------------------------------------------------
# The purge itself.
# --------------------------------------------------------------------------------------


async def test_the_guests_pii_is_gone_from_every_column_of_the_booking(
    sqlite_session: AsyncSession,
) -> None:
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    await sqlite_session.refresh(booking)
    assert booking.guest_name == ERASED_NAME
    assert booking.guest_email == ERASED_EMAIL
    assert booking.guest_phone is None
    assert booking.guest_phone_consent_at is None
    assert booking.guest_notes is None
    assert booking.answers == {}
    assert booking.guest_timezone == "UTC"


async def test_the_booking_itself_survives_the_purge_and_keeps_its_slot(
    sqlite_session: AsyncSession,
) -> None:
    """==Keep the fact, drop the person.== The appointment happened: it occupied that half-hour, it
    is in the host's history and in their accounting. Deleting the row would rewrite the past and
    free a slot that was genuinely taken. What is erased is the PERSON, not the event."""
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    survivor = await sqlite_session.get(Booking, booking.id)
    assert survivor is not None
    assert survivor.status is BookingStatus.CONFIRMED
    assert survivor.event_type_id == event_type.id


async def test_the_outbox_is_emptied_so_nothing_can_still_be_sent_to_them(
    sqlite_session: AsyncSession,
) -> None:
    """The queue exists only to send this person messages. After an erasure there are none left to
    send, and a queued intent still carries their address to a provider. It goes, payload too."""
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    remaining = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.booking_id == booking.id))
    ).all()
    assert list(remaining) == []


async def test_the_guest_tokens_and_the_send_ledger_are_gone(sqlite_session: AsyncSession) -> None:
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    tokens = (
        await sqlite_session.scalars(
            sa.select(GuestToken).where(GuestToken.booking_id == booking.id)
        )
    ).all()
    ledger = (
        await sqlite_session.scalars(
            sa.select(SentNotification).where(SentNotification.booking_id == booking.id)
        )
    ).all()
    assert list(tokens) == []
    assert list(ledger) == []


async def test_the_webhook_delivery_payload_is_redacted_but_the_record_stands(
    sqlite_session: AsyncSession,
) -> None:
    """==The one a purge always misses.== ``webhook_deliveries`` has NO ``booking_id`` column: the
    guest's whole serialised booking is buried in a JSON blob keyed by webhook, so a purge walking
    the foreign keys never reaches it — and never notices that it did not.

    The record that a subscriber was notified is kept (it is a fact about our integration); the
    person is stripped out of it, at any depth."""
    tenant, event_type = await _tenant(sqlite_session, "acme")
    await _guest_booking(sqlite_session, tenant, event_type)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    delivery = (await sqlite_session.scalars(sa.select(WebhookDelivery))).one()
    data = delivery.payload["data"]
    assert delivery.event == "booking.created"  # the fact survives
    assert data["guest_name"] == ERASED_NAME
    assert data["guest_email"] == ERASED_EMAIL
    assert data["guest_notes"] is None
    assert data["answers"] == {}  # nested one level deeper, and still found


async def test_after_the_purge_the_guest_appears_nowhere_in_the_database(
    sqlite_session: AsyncSession,
) -> None:
    """==The catch-all — the only test that can fail for a table nobody thought about.=="""
    tenant, event_type = await _tenant(sqlite_session, "acme")
    await _guest_booking(sqlite_session, tenant, event_type)
    before = await _everything_in_the_database(sqlite_session)
    # The haystack really does contain the needles — otherwise this test proves nothing at all.
    assert _EMAIL in before and _NAME in before and _PHONE in before and _ANSWER in before

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    after = await _everything_in_the_database(sqlite_session)
    assert _EMAIL not in after
    assert _NAME not in after
    assert _PHONE not in after
    assert _NOTES not in after
    assert _ANSWER not in after


async def test_the_purge_reports_what_it_touched(sqlite_session: AsyncSession) -> None:
    tenant, event_type = await _tenant(sqlite_session, "acme")
    await _guest_booking(sqlite_session, tenant, event_type)
    await _guest_booking(sqlite_session, tenant, event_type, start=_START + timedelta(days=1))

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    assert report.bookings == 2
    assert report.outbox_intents == 2
    assert report.guest_tokens == 2
    assert report.sent_notifications == 2
    assert report.webhook_deliveries == 2


async def test_purging_a_guest_who_was_never_here_changes_nothing(
    sqlite_session: AsyncSession,
) -> None:
    tenant, event_type = await _tenant(sqlite_session, "acme")
    await _guest_booking(sqlite_session, tenant, event_type)

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email="nobody@example.com")

    assert report.bookings == 0
    assert _EMAIL in await _everything_in_the_database(sqlite_session)


async def test_the_email_match_is_case_insensitive(sqlite_session: AsyncSession) -> None:
    """Somebody who booked as ``Ada@Example.COM`` and writes in as ``ada@example.com`` is the same
    person — and an erasure that misses on capitalisation is an erasure that did not happen."""
    tenant, event_type = await _tenant(sqlite_session, "acme")
    await _guest_booking(sqlite_session, tenant, event_type, email="Ada@Example.COM")

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email="ada@example.com")

    assert report.bookings == 1
    assert "Ada@Example.COM" not in await _everything_in_the_database(sqlite_session)


# --------------------------------------------------------------------------------------
# The isolation belt. ==This is why --tenant is mandatory.==
# --------------------------------------------------------------------------------------


async def test_the_purge_never_crosses_into_another_business(sqlite_session: AsyncSession) -> None:
    """==The reason the CLI refuses to run unscoped.== One person can be a guest of several
    businesses on one instance — the same email, in two tenants, with no relationship between them.
    An unscoped purge would erase them from a business that never received the request, destroying
    that host's records on somebody else's say-so."""
    acme, acme_event = await _tenant(sqlite_session, "acme")
    other, other_event = await _tenant(sqlite_session, "beta")
    await _guest_booking(sqlite_session, acme, acme_event)
    theirs = await _guest_booking(sqlite_session, other, other_event)

    await purge_guest(sqlite_session, tenant_id=acme.id, email=_EMAIL)

    await sqlite_session.refresh(theirs)
    assert theirs.guest_email == _EMAIL
    assert theirs.guest_name == _NAME
    assert theirs.guest_notes == _NOTES
    survivors = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.tenant_id == other.id))
    ).all()
    assert len(list(survivors)) == 1  # the other business's queue is untouched


async def test_the_cli_refuses_to_purge_without_a_real_tenant(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The CLI has no isolation belt of its own — it runs as the database owner, across every
    business on the instance. So the scope is not a filter it may fall back from: an unresolvable
    tenant is a HARD STOP, and an empty ``--tenant ''`` resolves to nothing and therefore stops."""
    async with sqlite_maker() as session, session.begin():
        tenant, event_type = await _tenant(session, "acme")
        await _guest_booking(session, tenant, event_type)

    for slug in ("", "   ", "ghost"):
        with pytest.raises(LookupError):
            await run_guest_purge(sqlite_maker, tenant_slug=slug, email=_EMAIL)

    async with sqlite_maker() as session:
        assert _EMAIL in await _everything_in_the_database(session)


async def test_the_cli_purges_the_named_tenant(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_maker() as session, session.begin():
        tenant, event_type = await _tenant(session, "acme")
        await _guest_booking(session, tenant, event_type)

    report = await run_guest_purge(sqlite_maker, tenant_slug="acme", email=_EMAIL)

    assert report.bookings == 1
    async with sqlite_maker() as session:
        assert _EMAIL not in await _everything_in_the_database(session)


# --------------------------------------------------------------------------------------
# The structural guards: the NEXT column, and the NEXT table.
# --------------------------------------------------------------------------------------


def test_every_guest_column_on_bookings_is_classified() -> None:
    """==The anti-drift lock on the columns.==

    A purge is written once, against the columns that existed that day. The next person adds
    ``guest_date_of_birth``, the purge knows nothing about it, every test still passes — and the
    erasure has quietly stopped being an erasure.

    So every ``guest_*`` column (plus ``answers``, which is guest-authored free text) must be
    CLASSIFIED: erased, with the value it is erased to, or deliberately retained. Not "swept up by a
    loop nobody reads" — DECIDED, by a person, in writing."""
    guest_columns = {
        column.name
        for column in Booking.__table__.columns
        if column.name.startswith("guest_") or column.name == "answers"
    }

    classified = set(BOOKING_PII_COLUMNS) | set(BOOKING_RETAINED_GUEST_COLUMNS)

    assert guest_columns == classified, (
        "a guest-bearing column on `bookings` is not classified by the purge: "
        f"{guest_columns ^ classified}. Add it to BOOKING_PII_COLUMNS (with the value it is erased "
        "to), or to BOOKING_RETAINED_GUEST_COLUMNS (with the reason it is not the guest's data)."
    )


def test_every_table_hanging_off_a_booking_is_handled_by_the_purge() -> None:
    """==The anti-drift lock on the tables.==

    The design names the rendered message bodies this batch introduces as one of the places the
    guest's data ends up. They do not exist yet — and that is exactly the shape of the defect: the
    table lands in another cut, nobody remembers the purge, and the erasure silently goes back to
    being partial while its own tests stay green.

    A table with a ``booking_id`` holds data about that booking's GUEST until proven otherwise. When
    the channels cut adds one, this fails until the purge accounts for it."""
    hanging_off_a_booking = {
        table.name
        for table in Base.metadata.sorted_tables
        if "booking_id" in table.columns and table.name != "bookings"
    }

    assert hanging_off_a_booking == set(TABLES_PURGED_BY_BOOKING), (
        "a table hangs off `bookings` and the guest purge does not handle it: "
        f"{hanging_off_a_booking ^ set(TABLES_PURGED_BY_BOOKING)}. A purge that misses one table "
        "passes its own test and leaves the guest's data behind — handle it in services/privacy.py "
        "and list it in TABLES_PURGED_BY_BOOKING."
    )
