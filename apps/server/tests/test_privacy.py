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

import ast
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    Payment,
    PaymentEvent,
    PaymentEventStatus,
    PaymentStatus,
    Schedule,
    SentNotification,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.services.outbox import (
    RETAINED_DEDUPE_KEY,
    RETAINED_PAYLOAD_KEYS,
    OutboxEffect,
    Purgeability,
    enqueue_effect,
    purge_policy,
    select_due,
)
from aethercal.server.services.payments import enqueue_refund, we_queued_this_refund
from aethercal.server.services.privacy import (
    BOOKING_PII_COLUMNS,
    BOOKING_RETAINED_GUEST_COLUMNS,
    ERASED_EMAIL,
    ERASED_NAME,
    JSON_PII_KEYS,
    RETAINED_OUTBOX_COLUMNS,
    TABLES_PURGED_BY_BOOKING,
    TABLES_RETAINED_OFF_BOOKING,
    purge_guest,
)

_EMAIL = "ada@example.com"
_NAME = "Ada Lovelace"
_PHONE = "+13055551234"
_NOTES = "Please call me on my mobile, I am Ada Lovelace."
_ANSWER = "A friend told me"
_SOURCE_IP = "203.0.113.9"
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
        # Confirmed ⇒ stamped: after B-05a a confirmed booking always carries when it became so.
        confirmed_at=start - timedelta(days=1),
        guest_name=_NAME,
        guest_email=email,
        guest_phone=_PHONE,
        guest_phone_consent_at=start,
        guest_timezone="America/New_York",
        guest_notes=_NOTES,
        answers={"how_did_you_hear": _ANSWER, "contact": email},
        source_ip=_SOURCE_IP,
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
    # ==Criterion 19.== The address the booking was made from is personal data, and it goes with the
    # person. It is also the one column here without a ``guest_`` prefix, so no derived lock
    # demanded
    # it — a person had to classify it, and this is the test that says they did.
    assert booking.source_ip is None


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

    missing = guest_columns - classified
    assert not missing, (
        f"a guest-bearing column on `bookings` is not classified by the purge: {sorted(missing)}. "
        "Add it to BOOKING_PII_COLUMNS (with the value it is erased to), or to "
        "BOOKING_RETAINED_GUEST_COLUMNS (with the reason it is not the guest's data)."
    )


def test_everything_the_purge_claims_to_erase_is_a_real_column() -> None:
    """==The lock's second half — and ``bookings.source_ip`` is why it now has one.==

    The half above derives what MUST be classified from the ``guest_`` prefix. It used to be a set
    EQUALITY, which quietly FORBADE the purge from classifying anything the prefix does not cover —
    and then ``source_ip`` arrived: personal data (roughly, where a person was), with no ``guest_``
    in its name, because it is an observation the SERVER made rather than a value the guest typed.

    Equality left two bad answers: rename the column to fit the test (and it would then have been
    swept into the reschedule's copy by ``guest_columns()`` for reasons nobody could later
    reconstruct), or leave a personal-data column out of the erasure entirely. So the lock is SPLIT:

    * every ``guest_*`` column must be CLASSIFIED — the anti-drift property, unchanged;
    * everything CLASSIFIED must be a REAL column — which catches the typo, the column somebody
      dropped, and the entry a rename left behind. Without this half, loosening the first one would
      have let ``BOOKING_PII_COLUMNS`` accumulate ghosts that erase nothing and report success.
    """
    columns = {column.name for column in Booking.__table__.columns}
    classified = set(BOOKING_PII_COLUMNS) | set(BOOKING_RETAINED_GUEST_COLUMNS)

    ghosts = classified - columns
    assert not ghosts, (
        f"the purge claims to erase columns that do not exist on `bookings`: {sorted(ghosts)}. A "
        "purge that writes to a ghost erases nothing — and reports success."
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

    # ==Two categories, and every booking_id table must be in EXACTLY one.== Deleted-outright
    # (``_PURGED_BY_BOOKING``: rows that exist only to message the guest), or retained-with-reason
    # (``TABLES_RETAINED_OFF_BOOKING``: a financial record kept for audit + idempotency, with no
    # guest PII to strip). The second category is the mirror of ``BOOKING_RETAINED_GUEST_COLUMNS``:
    # "keep this one" has to be a decision written down, never an omission that looks like a bug.
    handled = set(TABLES_PURGED_BY_BOOKING) | set(TABLES_RETAINED_OFF_BOOKING)
    assert not (set(TABLES_PURGED_BY_BOOKING) & set(TABLES_RETAINED_OFF_BOOKING)), (
        "a table is BOTH deleted and retained by the purge — one row cannot be two decisions"
    )
    assert hanging_off_a_booking == handled, (
        "a table hangs off `bookings` and the guest purge does not handle it: "
        f"{hanging_off_a_booking ^ handled}. A purge that misses one table passes its own test and "
        "leaves the guest's data behind — either DELETE its rows (list it in "
        "TABLES_PURGED_BY_BOOKING and wire it in services/privacy.py) or RETAIN it with a written "
        "reason (TABLES_RETAINED_OFF_BOOKING, for a financial record with no guest PII)."
    )


async def _pay(
    session: AsyncSession,
    booking: Booking,
    *,
    provider_ref: str = "pi_test_123",
    email: str = _EMAIL,
) -> None:
    """A paid payment for ``booking`` + an event whose raw payload carries the guest email."""
    session.add(
        Payment(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            provider="stripe",
            provider_ref=provider_ref,
            status=PaymentStatus.PAID,
            amount_cents=5000,
            currency="usd",
        )
    )
    session.add(
        PaymentEvent(
            tenant_id=booking.tenant_id,
            provider="stripe",
            event_id=f"evt_{uuid.uuid4().hex}",
            provider_ref=provider_ref,
            # A Stripe event puts PII under keys we do not control (``customer_details.email``).
            payload={"type": "payment_intent.succeeded", "customer_details": {"email": email}},
            status=PaymentEventStatus.APPLIED,
        )
    )
    await session.flush()


async def test_the_payment_record_survives_but_its_event_payload_is_redacted(
    sqlite_session: AsyncSession,
) -> None:
    """The MONEY record stays (audit + idempotency); the event's raw payload loses the guest.

    ``payments`` names no person and its UNIQUE is the anti-double-charge key, so it is kept. The
    ``payment_events`` row also stands — deleting it would destroy the anti-replay UNIQUE, letting
    same provider event apply again — but its payload, which can hold a customer email, is cleared.
    """
    tenant, event_type = await _tenant(sqlite_session, "biz")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _pay(sqlite_session, booking)

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    # The payment ledger row is untouched: it is a financial record, and it names nobody.
    payment = (
        await sqlite_session.scalars(sa.select(Payment).where(Payment.booking_id == booking.id))
    ).one()
    assert payment.status is PaymentStatus.PAID
    assert payment.provider_ref == "pi_test_123"

    # The event ROW stands (anti-replay key intact) but its payload carries no PII any more.
    event = (
        await sqlite_session.scalars(
            sa.select(PaymentEvent).where(PaymentEvent.provider_ref == "pi_test_123")
        )
    ).one()
    assert event.payload == {"redacted": True}
    assert _EMAIL not in str(event.payload)
    assert report.payment_events == 1


async def test_a_purge_never_orphans_a_charged_payment_by_redacting_a_parked_event(
    sqlite_session: AsyncSession,
) -> None:
    """==r6 finding 2 — the invariant that outranks the erasure.== A PARKED event has NOT been
    applied yet: the parked tick re-runs the arbiter from ITS payload to confirm or refund the
    charge. Redacting it would destroy the amount + currency the arbiter needs and leave a payment
    that was CHARGED but never confirmed nor refunded — the worst outcome this system can produce.

    So the purge redacts only TERMINAL events (``applied``/``dead``) and leaves an in-flight one
    applyable. Retaining it costs no privacy: it holds financial identifiers, not guest PII, and a
    later purge pass redacts it once the tick has driven it terminal.
    """
    tenant, event_type = await _tenant(sqlite_session, "biz")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _pay(sqlite_session, booking)  # the payment + one APPLIED (terminal) event

    # A second event for the SAME charge that the arbiter has NOT applied yet — it is waiting for
    # the tick. Its payload is exactly what the arbiter needs to re-run.
    parked_payload: dict[str, object] = {
        "kind": "paid",
        "provider_ref": "pi_test_123",
        "amount_cents": 5000,
        "currency": "usd",
    }
    sqlite_session.add(
        PaymentEvent(
            tenant_id=tenant.id,
            provider="stripe",
            event_id=f"evt_parked_{uuid.uuid4().hex}",
            provider_ref="pi_test_123",
            payload=dict(parked_payload),
            status=PaymentEventStatus.PARKED,
        )
    )
    await sqlite_session.flush()

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    events = (
        await sqlite_session.scalars(
            sa.select(PaymentEvent).where(PaymentEvent.provider_ref == "pi_test_123")
        )
    ).all()
    by_status = {row.status: row for row in events}

    # ==The parked event is still APPLYABLE== — the arbiter can still confirm or refund the charge.
    parked = by_status[PaymentEventStatus.PARKED]
    assert parked.payload == parked_payload, "a parked event must stay applyable after a purge"

    # And the purge is still COMPLETE for what is done with: the terminal event is redacted.
    applied = by_status[PaymentEventStatus.APPLIED]
    assert applied.payload == {"redacted": True}
    assert _EMAIL not in str(applied.payload)
    assert report.payment_events == 1, "only the terminal event is redacted"


# --- B-05c: the erasure must not keep the guest's money -----------------------------------------
#
# The purge deleted EVERY outbox row of the booking, with no filter on effect. That is right for the
# three effects that exist only to message the person — and wrong for the one that exists to give
# them their money back. See `test_a_queued_refund_survives_the_purge...` for the full argument.


async def _owe_them_a_refund(
    session: AsyncSession, booking: Booking, *, provider_ref: str = "pi_owed_777"
) -> None:
    """A charge of theirs, and the REFUND we owe on it — queued through the REAL path.

    Through ``enqueue_refund`` rather than a hand-built ``Outbox(...)``: the payload this must
    survive with is the one the product actually writes, not one a test author imagined.
    """
    session.add(
        Payment(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            provider="stripe",
            provider_ref=provider_ref,
            status=PaymentStatus.PAID,
            amount_cents=5000,
            currency="usd",
        )
    )
    await session.flush()
    await enqueue_refund(session, booking=booking, provider="stripe", provider_ref=provider_ref)


async def test_a_queued_refund_survives_the_purge_and_the_drain_still_runs_it(
    sqlite_session: AsyncSession,
) -> None:
    """==The erasure gives them their money back; it does not keep it.==

    The guest cancels a paid booking (a REFUND intent is queued) and, before the drain reaches it,
    asks to be forgotten. The purge used to delete every outbox row of the booking — so the intent
    vanished, the drain never saw it, and the guest was left erased AND out of pocket. Silently:
    the purge reported success, and every test passed.

    A REFUND is not a message. It carries ``{provider, provider_ref}`` and names nobody.

    The assertion is on the EFFECTIVE state, not on the row: what matters is not that a row is still
    there, it is that ``select_due`` — the drain's own planner — still hands it to a worker.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _owe_them_a_refund(sqlite_session, booking)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    survivors = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.booking_id == booking.id))
    ).all()
    assert [row.effect for row in survivors] == [OutboxEffect.REFUND.value], (
        "the GOOGLE intent must go (it exists only to message them) and the REFUND must stay"
    )
    refund = survivors[0]
    # The payload the runner needs is intact — a surviving row that lost it is the same bug.
    assert refund.payload == {"provider": "stripe", "provider_ref": "pi_owed_777"}
    # ==The effective state.== The drain's planner still plans it, so the money still moves.
    assert refund.id in await select_due(sqlite_session, now=_START + timedelta(days=2))


async def test_the_purge_leaves_the_discriminator_of_our_own_refund_standing(
    sqlite_session: AsyncSession,
) -> None:
    """==The r8 discriminator is the refund intent, so the purge deleting it re-opened r8.==

    ``we_queued_this_refund`` answers "is the refund the provider is telling us about OURS?" by the
    existence of the intent — deliberately, because it is the only fact committed before the
    provider is called. Purge the intent and the answer silently flips to "no": the next
    ``charge.refunded`` reads as an operator's out-of-band refund and CANCELS the booking.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _owe_them_a_refund(sqlite_session, booking)

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    assert await we_queued_this_refund(
        sqlite_session, tenant_id=tenant.id, provider_ref="pi_owed_777"
    ), "the erasure destroyed the fact that the refund in flight is ours"


@pytest.mark.parametrize("effect", list(OutboxEffect))
async def test_the_purge_deletes_exactly_what_the_policy_calls_purgeable(
    sqlite_session: AsyncSession, effect: OutboxEffect
) -> None:
    """==The anti-drift lock on the EFFECTS, and the reason it is parametrised over the enum.==

    Not a list of the effects that exist today — a sweep of ``OutboxEffect`` itself. The effect
    somebody adds next is covered by this test the moment they add it, and it fails until
    ``purge_policy`` classifies it. That is the whole difference between a lock and a photograph.

    The assertion is the EFFECTIVE state (did the row actually go?), never the declaration read back
    to itself.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    probe = f"probe:{effect.value}"
    sqlite_session.add(
        Outbox(
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=effect.value,
            dedupe_key=probe,
            payload={
                "provider": "stripe",
                "provider_ref": "pi_probe",
                "booking_id": str(booking.id),
            },
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
    )
    await sqlite_session.flush()

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    survived = (
        await sqlite_session.scalars(
            sa.select(Outbox).where(Outbox.booking_id == booking.id, Outbox.dedupe_key == probe)
        )
    ).all()
    was_deleted = list(survived) == []
    assert was_deleted is (purge_policy(effect) is Purgeability.PURGEABLE), (
        f"{effect.value} is classified {purge_policy(effect).value} but the purge "
        f"{'deleted' if was_deleted else 'kept'} it — the declaration and the mechanism disagree"
    )


def test_every_outbox_effect_declares_whether_the_purge_deletes_it() -> None:
    """The exhaustiveness lock: ``purge_policy`` must answer for EVERY effect, not raise.

    ``assert_never`` already makes this a type error, but pyright is not the only reader — this
    fails the suite too, in the same run as the behaviour it guards.
    """
    for effect in OutboxEffect:
        assert isinstance(purge_policy(effect), Purgeability), (
            f"{effect.value} has no purge classification"
        )


def test_a_retained_effect_declares_the_payload_keys_it_may_carry() -> None:
    """==The second half of retaining a row: proving it holds no PII.==

    An effect is RETAINED because its payload names nobody. That is a claim about the payload, and a
    claim nobody checks is a claim that decays: add ``guest_email`` to the refund payload tomorrow
    and the purge would faithfully RETAIN it — erasure reported complete, address still in the
    table.

    So a RETAINED effect must declare its keys, and the declaration must be PII-free. Both halves
    are derived: the set of retained effects comes from ``purge_policy``, and the PII vocabulary
    from ``JSON_PII_KEYS`` — the same one the redactor uses.
    """
    retained = {effect for effect in OutboxEffect if purge_policy(effect) is Purgeability.RETAINED}
    undeclared = retained ^ set(RETAINED_PAYLOAD_KEYS)
    assert retained == set(RETAINED_PAYLOAD_KEYS), (
        "a RETAINED effect must declare the payload keys it may carry, or it is a row the purge "
        f"keeps without anybody having checked what is in it: {undeclared}"
    )
    for effect, keys in RETAINED_PAYLOAD_KEYS.items():
        leaks = keys & set(JSON_PII_KEYS)
        assert not leaks, (
            f"{effect.value} is RETAINED by the purge but declares PII key(s) {leaks}. A row the "
            "erasure keeps cannot carry the person it erased — either the key goes, or the effect "
            "is not retainable."
        )


async def test_the_funnel_refuses_a_retained_effect_carrying_an_undeclared_key(
    sqlite_session: AsyncSession,
) -> None:
    """==Gate the FUNNEL, not the callers.== ``enqueue_effect`` is where every Outbox row in the
    source tree is built, so guarding it there catches the enqueue path nobody has written yet —
    without its author ever learning this rule exists.

    A RETAINED row outlives an erasure. Whether it may is decided by what is IN it, so a key nobody
    classified cannot ride along on the assumption that it is harmless.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    with pytest.raises(ValueError, match="undeclared payload key"):
        await enqueue_effect(
            sqlite_session,
            booking=booking,
            effect=OutboxEffect.REFUND,
            dedupe_key="refund:sneaky",
            payload={"provider": "stripe", "provider_ref": "pi_x", "guest_email": _EMAIL},
        )


async def test_a_retained_row_written_before_the_guard_is_still_redacted(
    sqlite_session: AsyncSession,
) -> None:
    """==The rows already in the database.== The funnel guard binds what is enqueued from now on; it
    says nothing about a row committed last year, when nothing stopped a payload growing a key.

    So the purge does not merely trust the classification — it REDACTS what it retains. The row
    survives (the money still moves) and the person does not (the erasure is complete). Same shape
    as ``payment_events``: keep the row, clear the person out of it.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    # Written straight to the table, exactly as a legacy row would sit there.
    sqlite_session.add(
        Outbox(
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=OutboxEffect.REFUND.value,
            dedupe_key="refund:legacy",
            payload={"provider": "stripe", "provider_ref": "pi_legacy", "guest_email": _EMAIL},
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
    )
    await sqlite_session.flush()

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    row = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.dedupe_key == "refund:legacy"))
    ).one()
    assert row.payload["provider_ref"] == "pi_legacy", "the money must still move"
    # DROPPED, not tombstoned: a retained payload is rebuilt from what it may KEEP, so a key that
    # names the guest simply is not carried over. A tombstone would still be a key nobody declared.
    assert "guest_email" not in row.payload, "the person must be gone"
    assert row.payload == {"provider": "stripe", "provider_ref": "pi_legacy"}
    assert report.outbox_payloads_redacted == 1


# --- B-05c re-Crisol: a retained payload is redacted by ALLOWLIST, not by denylist ---------------


async def _legacy_retained_row(
    session: AsyncSession, booking: Booking, *, effect: str, payload: dict[str, object]
) -> None:
    """A row written straight to the table, as one committed before the funnel guard existed."""
    session.add(
        Outbox(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            effect=effect,
            dedupe_key=f"legacy:{effect}:{uuid.uuid4().hex[:6]}",
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempts=0,
        )
    )
    await session.flush()


async def test_a_retained_payload_loses_a_pii_key_the_redactor_never_heard_of(
    sqlite_session: AsyncSession,
) -> None:
    """==A denylist is a photograph, exactly like a list of instances.==

    ``JSON_PII_KEYS`` knows ``guest_email``. It does not know ``receipt_email``, ``billing_name``,
    or whatever the next person calls the address they put in a refund payload — and a redactor
    built on "the keys I know how to erase" leaves behind every key it has not met. On a row the
    purge DELETES that costs nothing (the row goes anyway). On a row the purge KEEPS it is a guest
    erasure that quietly kept the guest.

    So a retained payload is rebuilt from what it is ALLOWED to keep (``RETAINED_PAYLOAD_KEYS``,
    which is exactly what the runner reads) rather than stripped of what we know to remove. An
    unrecognised key is dropped for the same reason an unclassified effect fails the build: nobody
    vouched for it.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect=OutboxEffect.REFUND.value,
        # `receipt_email` is NOT in JSON_PII_KEYS — the denylist has never heard of it.
        payload={
            "provider": "stripe",
            "provider_ref": "pi_legacy",
            "receipt_email": _EMAIL,
            "billing_name": _NAME,
        },
    )

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    row = (
        await sqlite_session.scalars(
            sa.select(Outbox).where(
                Outbox.booking_id == booking.id, Outbox.effect == OutboxEffect.REFUND.value
            )
        )
    ).one()
    # What the runner needs survives, whole.
    assert row.payload == {"provider": "stripe", "provider_ref": "pi_legacy"}, (
        "the retained payload must be exactly what the refund runner reads — no more"
    )
    assert report.outbox_payloads_redacted == 1


async def test_a_retained_row_of_an_unrecognised_effect_loses_its_payload_wholesale(
    sqlite_session: AsyncSession,
) -> None:
    """==Fail CLOSED on an effect nothing can vouch for.==

    An effect string the enum no longer knows (somebody removed a member; a hand-written row) is not
    deleted — the purge does not destroy what it cannot classify — but nothing can say which of its
    keys name a person either. So there is no allowlist to keep, and the payload goes wholesale, the
    same way ``payment_events`` does with a provider schema that is not ours.

    It loses nothing operational: an effect with no enum member has no handler, so the row was never
    going to run.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect="carrier_pigeon",  # never an OutboxEffect
        payload={"address": _EMAIL, "note": _NOTES},
    )

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    row = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.effect == "carrier_pigeon"))
    ).one()
    assert row.payload == {"redacted": True}
    assert _EMAIL not in str(row.payload)
    assert _NOTES not in str(row.payload)


async def test_an_ordinary_purge_reports_no_anomaly_and_says_nothing_about_one(
    sqlite_session: AsyncSession,
) -> None:
    """==The other half of an alarm: it must be SILENT when nothing is wrong.==

    A refund whose payload is exactly what it should be is not an anomaly, and a purge that
    announced one every time would train the reader to skip the line — which is how the real one
    gets missed. The count is 0 and there is nothing to tell the operator.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _owe_them_a_refund(sqlite_session, booking)

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    assert report.outbox_retained == 1, "the refund is kept"
    assert report.outbox_payloads_redacted == 0, "and it is not an anomaly"
    assert report.purge_anomalies == [], "so there is nothing to tell the operator"


async def test_an_anomaly_reaches_the_operator_and_says_WHICH_ROW(
    sqlite_session: AsyncSession,
) -> None:
    """==An anomaly nobody is told about is the silent no-op wearing a hat.==

    The purge stripping an undeclared key means a row got past the funnel guard — because it
    predates it, or because something wrote to the table directly. Either way it is a fact about the
    erasure that a human has to be able to act on, and the count alone ("1 payload redacted") cannot
    be acted on at all.

    ==What makes it actionable is the ROW, not the key.== This test used to demand the key by name
    ("the operator needs to know WHICH key was found") — and that demand was itself the defect a
    later review caught: an unrecognised key can BE the guest's address, so quoting it re-publishes
    what the purge just erased. The intent's id is a UUID we generated, it names nobody, and the row
    is RETAINED — so it is the thread an operator actually pulls: read its ``created_at`` and
    ``dedupe_key``, and find the write path that produced it. Which key it was is not recoverable,
    and must not be: it was the person.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect=OutboxEffect.REFUND.value,
        payload={"provider": "stripe", "provider_ref": "pi_legacy", "receipt_email": _EMAIL},
    )
    row_id = (
        await sqlite_session.scalars(
            sa.select(Outbox.id).where(
                Outbox.booking_id == booking.id, Outbox.effect == OutboxEffect.REFUND.value
            )
        )
    ).one()

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    assert report.purge_anomalies, "an anomaly that reaches nobody may as well not be detected"
    anomaly = report.purge_anomalies[0]
    assert OutboxEffect.REFUND.value in anomaly
    assert str(row_id) in anomaly, (
        "without the row there is nothing for the operator to go and read"
    )
    # The anomaly line is evidence, and evidence naming the erased person is the one thing an
    # erasure must not write down — whether that person is in a value OR in a key.
    assert _EMAIL not in anomaly
    assert _NAME not in anomaly


# --- The inference that "gone means purged" rests on exactly one fact ---------------------------


_SRC = Path(__file__).resolve().parents[1] / "src" / "aethercal" / "server"
_PRIVACY = _SRC / "services" / "privacy.py"


def _shipped_modules() -> list[Path]:
    """Every module of the shipped server source. The Alembic versions are generated: excluded."""
    return [
        path
        for path in sorted(_SRC.rglob("*.py"))
        if "migrations" not in path.parts and "__pycache__" not in path.parts
    ]


def _deletes(tree: ast.Module, model: str) -> list[int]:
    """Line numbers of any ``delete(<model>)`` / ``sa.delete(<model>)`` in this module."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "delete":
            continue
        hits += [node.lineno for arg in node.args if isinstance(arg, ast.Name) and arg.id == model]
    return hits


def test_the_guest_purge_is_the_only_thing_that_deletes_an_outbox_row() -> None:
    """==The lock under ``_why_it_vanished``, and the reason it is an AST walk.==

    A deleted row leaves nothing to read, so "the settle found no row" cannot be OBSERVED to mean
    "a guest erasure took it" — it is inferred. The inference is sound today for one reason only:
    ``services/privacy.py`` holds the sole ``DELETE`` against ``outbox`` in the entire source tree.

    That is exactly the shape of fact that rots. Add a second deleter — a retention sweep, an admin
    "clear the queue" button — and ``_why_it_vanished`` keeps answering ``PURGED``, in silence, for
    rows no erasure ever touched: routine-looking, unalarmed, and wrong. So the inference is not
    left to a comment somebody has to remember to keep true. It is asserted.
    """
    offenders = {
        path.relative_to(_SRC).as_posix(): lines
        for path in _shipped_modules()
        if path != _PRIVACY
        and (lines := _deletes(ast.parse(path.read_text(encoding="utf-8")), "Outbox"))
    }

    assert offenders == {}, (
        "something other than the guest purge deletes outbox rows: "
        f"{offenders}. `_why_it_vanished` (services/outbox.py) reads a missing row as PURGED — "
        "routine, unalarmed — because the purge was the only thing that could have removed it. A "
        "second deleter makes that classification lie: its vanished rows would be filed as "
        "ordinary erasures instead of reaching anybody. Either delete through the purge, or give "
        "`Vanished` a fourth cause and classify it."
    )


def test_nothing_deletes_a_booking_out_from_under_its_intents() -> None:
    """The back door: ``outbox.booking_id`` is ``ON DELETE CASCADE``.

    A ``DELETE`` on ``bookings`` removes that booking's intents with no ``delete(Outbox)`` for the
    lock above to catch — and the erasure deliberately REDACTS bookings rather than deleting them
    (the appointment happened; the slot was genuinely taken). So nothing should be deleting one at
    all, and if that ever changes, the cascade becomes a second, invisible deleter — of the REFUND
    a guest is owed, among other things.
    """
    offenders = {
        path.relative_to(_SRC).as_posix(): lines
        for path in _shipped_modules()
        if (lines := _deletes(ast.parse(path.read_text(encoding="utf-8")), "Booking"))
    }

    assert offenders == {}, (
        f"something deletes bookings: {offenders}. `outbox.booking_id` is ON DELETE CASCADE, so "
        "that silently deletes their queued intents — including a REFUND the guest is owed — "
        "through a door neither `purge_policy` nor the lock above can see."
    )


# --- B-05c re-Crisol r2: a payload that is not an object, and the claimed-row decision -----------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(["pi_legacy", _EMAIL], id="a-list"),
        pytest.param("pi_legacy", id="a-string"),
        pytest.param(42, id="a-number"),
        pytest.param(None, id="null"),
        pytest.param(True, id="a-bool"),
    ],
)
async def test_a_retained_payload_that_is_not_an_object_is_redacted_not_crashed(
    sqlite_session: AsyncSession, payload: object
) -> None:
    """==A purge that raises is an erasure that DID NOT HAPPEN.==

    ``payload`` is a JSON column, and JSON is not "object": a list, a string, a number, ``true`` or
    ``null`` are all valid in that column, and a row written before the funnel guard (or by hand, or
    by an effect since retired) can hold any of them. The allowlist rebuild assumed a ``dict``, so
    anything else blew up mid-purge — and then the erasure request goes unanswered, which is worse
    than any single leaked key: nothing at all gets erased, for that guest, in that tenant.

    The rule is the one already applied to the effect with no allowlist: ==a shape nobody can vouch
    for is a shape we do not keep.== There is no allowlist to apply to a scalar — it has no keys —
    so the payload goes wholesale.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session, booking, effect=OutboxEffect.REFUND.value, payload=payload
    )

    # The purge must COMPLETE. That is the assertion; the row below is the detail.
    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    assert report.bookings == 1, "the erasure must have happened at all"
    row = (
        await sqlite_session.scalars(
            sa.select(Outbox).where(
                Outbox.booking_id == booking.id, Outbox.effect == OutboxEffect.REFUND.value
            )
        )
    ).one()
    assert row.payload == {"redacted": True}
    assert _EMAIL not in str(row.payload)
    # And the booking itself was still erased — a payload of the wrong shape must not be able to
    # abort the rest of the purge.
    await sqlite_session.refresh(booking)
    assert booking.guest_email == ERASED_EMAIL


async def test_the_purge_deletes_a_message_intent_a_worker_is_mid_send_on(
    sqlite_session: AsyncSession,
) -> None:
    """==The purge does NOT spare a claimed row, and that is a DECISION (B-05c re-Crisol r2).==

    It looks like one worth reconsidering: a worker holds this row's lease and is on the wire with
    it, and ``_purge_payment_events`` sets an apparent precedent by RETAINING a still-actionable
    event for "a later purge pass". The precedent is a trap, and the reason is a fact about this
    repo rather than a matter of taste: ==there IS no later purge pass.== ``purge_guest`` is reached
    only from ``run_guest_purge``, which is reached only from the ``guest purge`` CLI command. The
    scheduler registers exactly three recurring jobs — webhook delivery, busy refresh, outbox drain
    — and no cron or timer anywhere invokes an erasure. It is a one-shot operator action.

    So "skip it, the next pass will get it" means NEVER, and the guest's address stays in the table
    for good. A legal obligation cannot rest on an operator remembering to run a command twice.
    The row goes.

    ==What deleting it does NOT do, and no design could:== a message already handed to the SMTP
    server will still arrive. Deleting the intent does not recall it, and neither would sparing it.
    See ``purge_guest``'s docstring — the erasure removes the DATA, and cannot un-send a message
    that has already left.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    # Exactly the state `claim_one` leaves: claimed, leased, and mid-flight on some worker.
    sqlite_session.add(
        Outbox(
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=OutboxEffect.EMAIL.value,
            dedupe_key="email:confirmation:inflight",
            payload={"kind": "confirmation", "guest_email": _EMAIL},
            status=OutboxStatus.CLAIMED.value,
            attempts=1,
            claimed_by="worker-7",
            lease_expires_at=_START + timedelta(minutes=5),
        )
    )
    await sqlite_session.flush()

    await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    survived = (
        await sqlite_session.scalars(
            sa.select(Outbox).where(Outbox.dedupe_key == "email:confirmation:inflight")
        )
    ).all()
    assert list(survived) == [], (
        "the purge spared a CLAIMED intent. There is no second purge pass in this system (the "
        "command is one-shot and no timer runs it), so 'the next pass will get it' means never — "
        "and this row carries the guest's address"
    )


# --- B-05c re-Crisol r3: the anomaly reporter must not itself leak the person -------------------


async def test_an_anomaly_never_names_a_key_that_could_BE_the_person(
    sqlite_session: AsyncSession,
) -> None:
    """==The reporter built to make a strange erasure visible must not be the leak.==

    Naming keys and never values sounds safe, and it is not: ==a key can BE the datum.== A dict
    keyed by address (``{"ada@example.com": {...}}`` — a group-by that got serialised, an old
    per-recipient map) puts the guest's email in the key position, and the anomaly line writes it
    to an ERROR log and to the operator's terminal, moments after the erasure removed it from the
    table. A log is usually replicated and outlives the database row, so that is the erasure
    undone, in the one artefact nobody thinks of as a database.

    The rule is the one this cut keeps re-learning: ==name only what is in OUR OWN vocabulary.== A
    key we declared somewhere is a constant in the source tree — printing it discloses nothing. A
    key we have never seen is data until proven otherwise, and it is counted, not quoted.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect=OutboxEffect.REFUND.value,
        # The guest's address IN THE KEY POSITION, and their phone too.
        payload={"provider": "stripe", "provider_ref": "pi_legacy", _EMAIL: True, _PHONE: 1},
    )

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    anomaly = report.purge_anomalies[0]
    assert _EMAIL not in anomaly, (
        "the anomaly line names the address the purge just erased — and it goes to an ERROR log, "
        "which is replicated and outlives the row"
    )
    assert _PHONE not in anomaly
    # Still ACTIONABLE: the operator learns the effect, the row, and that two keys were dropped.
    assert OutboxEffect.REFUND.value in anomaly
    assert "2 unrecognised" in anomaly
    # And the erasure itself still did its job on the payload.
    row = (
        await sqlite_session.scalars(sa.select(Outbox).where(Outbox.booking_id == booking.id))
    ).one()
    assert row.payload == {"provider": "stripe", "provider_ref": "pi_legacy"}


async def test_an_anomaly_names_the_keys_that_are_our_own_vocabulary(
    sqlite_session: AsyncSession,
) -> None:
    """==And it must still be worth reading.== A count alone is an alarm nobody can act on.

    ``guest_email`` is not the guest's email — it is a column name we chose, sitting in
    ``JSON_PII_KEYS`` in this very module. Printing it tells the operator the most useful thing
    there is (a legacy refund payload was carrying the guest's address) and discloses nothing that
    is not already in the source tree.

    Both halves are DERIVED from declarations that already exist: our vocabulary is
    ``JSON_PII_KEYS`` plus everything ``RETAINED_PAYLOAD_KEYS`` declares. No new hand-written list
    of "safe words" — those rot exactly like the denylist did.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect=OutboxEffect.REFUND.value,
        payload={
            "provider": "stripe",
            "provider_ref": "pi_legacy",
            "guest_email": _EMAIL,  # our word, in the key position; its VALUE is the address
            "booking_id": str(booking.id),  # our word, declared for EXPIRE_HOLD
            "receipt_email": _EMAIL,  # NOT our word — could be anything
        },
    )

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    anomaly = report.purge_anomalies[0]
    assert "guest_email" in anomaly, "our own schema words are safe to print, and the useful part"
    assert "booking_id" in anomaly
    assert "1 unrecognised" in anomaly, "and the one we cannot vouch for is counted, not quoted"
    assert _EMAIL not in anomaly, "the VALUE never appears, whatever the key is called"
    assert "receipt_email" not in anomaly


async def test_the_anomaly_lines_carry_no_trace_of_the_guest_at_all(
    sqlite_session: AsyncSession,
) -> None:
    """==The catch-all, pointed at the report instead of the database.==

    ``test_after_the_purge_the_guest_appears_nowhere_in_the_database`` proves the tables are clean.
    It says nothing about what the purge SAID while cleaning them — and the report goes to a log and
    a terminal, which is where a leak is least likely to be looked for and most likely to be kept.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)
    await _legacy_retained_row(
        sqlite_session,
        booking,
        effect=OutboxEffect.REFUND.value,
        payload={
            "provider": "stripe",
            "provider_ref": "pi_legacy",
            _EMAIL: True,
            _NAME: True,
            _PHONE: True,
            _NOTES: True,
            "answers": {"contact": _EMAIL},
        },
    )

    report = await purge_guest(sqlite_session, tenant_id=tenant.id, email=_EMAIL)

    said = " ".join(report.purge_anomalies)
    assert report.purge_anomalies, "the haystack must contain something, or this proves nothing"
    for needle in (_EMAIL, _NAME, _PHONE, _NOTES, _ANSWER):
        assert needle not in said, f"the purge said {needle!r} out loud while erasing it"


# --- B-05c re-Crisol r4: every COLUMN a retained row carries, not just the payload -------------


_SAMPLE_RETAINED_PAYLOAD: dict[OutboxEffect, dict[str, object]] = {
    OutboxEffect.REFUND: {"provider": "stripe", "provider_ref": "pi_sample"},
    OutboxEffect.EXPIRE_HOLD: {"booking_id": "6f1c9a2e-0000-4000-8000-000000000001"},
}
"""One payload per RETAINED effect, shaped exactly as its declared allowlist. Exhaustiveness is
asserted below, so a newly retained effect must bring its sample rather than be skipped."""


def test_every_column_a_retained_row_carries_is_classified() -> None:
    """==The question the payload allowlist never asked: what ELSE does the row carry?==

    Three rounds went into ``payload`` — an allowlist, a shape, a vocabulary — while the row it sits
    on has twelve other columns, and a RETAINED row keeps every one of them. ``dedupe_key`` is a
    free-form ``VARCHAR(128)`` built from whatever the caller passed, and nothing had ever looked at
    it. That is this batch's own doctrine arriving at its author: audit what the spec never looked
    at.

    So the classification is DERIVED FROM THE MODEL, not from memory — ``Outbox.__table__.columns``
    is the source, exactly as ``BOOKING_PII_COLUMNS`` derives its own from ``bookings``. Add a
    column tomorrow and this fails until somebody writes down why a retained row may keep it.
    """
    on_the_model = {column.name for column in Outbox.__table__.columns}

    assert on_the_model == set(RETAINED_OUTBOX_COLUMNS), (
        "a column of `outbox` is not classified, and a RETAINED row keeps every column it has: "
        f"{on_the_model ^ set(RETAINED_OUTBOX_COLUMNS)}. Say why the erasure may keep it (and what "
        "makes that true), or the row cannot be retained as it stands."
    )
    for column, reason in RETAINED_OUTBOX_COLUMNS.items():
        assert reason.strip(), f"{column} is classified with an empty reason, which is no reason"


def test_every_retained_effect_brings_a_sample_payload() -> None:
    """The sample table above is exhaustive over the RETAINED effects, or the test below lies by
    omission: a newly retained effect would simply never be checked."""
    retained = {effect for effect in OutboxEffect if purge_policy(effect) is Purgeability.RETAINED}
    assert retained == set(_SAMPLE_RETAINED_PAYLOAD)
    assert retained == set(RETAINED_DEDUPE_KEY), (
        "a RETAINED effect must declare how its dedupe_key is rebuilt from its payload: "
        f"{retained ^ set(RETAINED_DEDUPE_KEY)}"
    )


@pytest.mark.parametrize(
    "effect", [effect for effect in OutboxEffect if purge_policy(effect) is Purgeability.RETAINED]
)
def test_a_retained_effects_dedupe_key_is_rebuildable_from_its_own_payload(
    effect: OutboxEffect,
) -> None:
    """==The invariant that makes ``dedupe_key`` safe, and the reason it is not an allowlist.==

    ``dedupe_key`` cannot be redacted the way ``payload`` is. ``we_queued_this_refund`` matches on
    ``(tenant_id, dedupe_key)`` — it IS the r8 discriminator, the committed fact that tells our own
    refund from an operator's — so rewriting it would stop the guest's money-in-flight from being
    recognised as ours and re-open the very bug this cut opened with. It has to survive verbatim.

    Something that survives verbatim has to be PII-free BY CONSTRUCTION, and this is what makes it
    checkable: ==a retained row's dedupe_key must be rebuildable from its own retained payload.==
    The payload is already proven to name nobody (``RETAINED_PAYLOAD_KEYS`` + the funnel guard), so
    a key derived from nothing else can carry nothing else. Derived, not asserted in prose.
    """
    rebuild = RETAINED_DEDUPE_KEY[effect]
    payload = _SAMPLE_RETAINED_PAYLOAD[effect]
    key = rebuild(payload)

    assert key, f"{effect.value} declares no way to rebuild its dedupe_key"
    # It is built from the DECLARED keys and nothing else: drop any one and it can no longer build
    # the same key. A rebuild that ignores its payload would be a rubber stamp.
    depends_on_payload = False
    for declared in RETAINED_PAYLOAD_KEYS[effect]:
        thinner = {k: v for k, v in payload.items() if k != declared}
        try:
            rebuilt = rebuild(thinner)
        except (KeyError, ValueError, TypeError):
            depends_on_payload = True
            continue
        if rebuilt != key:
            depends_on_payload = True
    assert depends_on_payload, (
        f"{effect.value}'s dedupe_key rebuild does not read its payload at all, so it proves "
        "nothing about where the key came from"
    )


async def test_the_funnel_refuses_a_retained_intent_whose_dedupe_key_it_cannot_rebuild(
    sqlite_session: AsyncSession,
) -> None:
    """==Gate the funnel, for the column nobody had looked at.==

    A ``dedupe_key`` the guard cannot rebuild from the declared payload came from somewhere nothing
    can vouch for — and it survives an erasure verbatim, in a UNIQUE index, for ever. A key like
    ``refund:ada@example.com`` is not a hypothetical shape: it is exactly what ``refund_dedupe_key``
    produces if a provider_ref is ever an address, or if a future caller keys a retained intent on
    "the person this is about".
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    with pytest.raises(ValueError, match="dedupe_key"):
        await enqueue_effect(
            sqlite_session,
            booking=booking,
            effect=OutboxEffect.REFUND,
            # Not rebuildable from the payload below: it names the guest instead of the charge.
            dedupe_key=f"refund:{_EMAIL}",
            payload={"provider": "stripe", "provider_ref": "pi_1"},
        )


async def test_the_real_refund_path_still_passes_the_dedupe_guard(
    sqlite_session: AsyncSession,
) -> None:
    """The guard must bind the REAL enqueue path, not only a test's idea of it.

    ``enqueue_refund`` builds its key with ``refund_dedupe_key(provider_ref)`` and its payload from
    the same ``provider_ref``. If the guard and the product ever disagreed about what "rebuildable
    from the payload" means, refunds would stop being queued at all — the one failure this whole cut
    exists to prevent, arriving through the guard meant to protect it.
    """
    tenant, event_type = await _tenant(sqlite_session, "acme")
    booking = await _guest_booking(sqlite_session, tenant, event_type)

    await _owe_them_a_refund(sqlite_session, booking, provider_ref="pi_guarded")

    rows = (
        await sqlite_session.scalars(
            sa.select(Outbox).where(Outbox.effect == OutboxEffect.REFUND.value)
        )
    ).all()
    assert len(list(rows)) == 1, "the real refund path was refused by its own guard"


def test_the_funnel_is_the_only_place_an_outbox_row_is_constructed() -> None:
    """==The premise every other guard on this row rests on.==

    ``enqueue_effect``'s docstring says ``Outbox(...)`` is built "here and nowhere else in the
    source tree", and everything leans on it: the confirmation gate, the retained-payload allowlist,
    the dedupe_key rebuild — and the anomaly reporter, which QUOTES ``row.effect`` on the assumption
    that the column can only ever hold a word we chose. Build a row anywhere else and ``effect``
    becomes free text that reaches an ERROR log unfiltered, while every guard above is bypassed in
    silence.

    It was a sentence in a docstring. Now it is asserted — that is the difference between a claim
    and a lock, and this whole cut is what happens when a true sentence quietly stops being true.
    """
    offenders: dict[str, list[int]] = {}
    for path in _shipped_modules():
        if path.name == "outbox.py" and path.parent.name == "services":
            continue  # The funnel itself.
        hits = [
            node.lineno
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Outbox"
        ]
        if hits:
            offenders[path.relative_to(_SRC).as_posix()] = hits

    assert offenders == {}, (
        f"an Outbox row is constructed outside `enqueue_effect`: {offenders}. That funnel is where "
        "the confirmation gate, the retained-payload allowlist and the dedupe_key rebuild all live "
        "— a row built elsewhere skips every one of them, silently, and puts unvalidated text in "
        "`effect`, which the purge's anomaly line quotes into a log."
    )
