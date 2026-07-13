"""The booking path actually reaches the host's calendar (RF-11) — and says so when it cannot.

``api/bookings.py`` used to state, in as many words, that "Google sync is not wired yet ... so
``connection`` stays ``None`` — a booking never attempts Google in this path". RF-11/12/13 were
ticked and were not true: **no booking had ever created an event in a host's calendar**. The Google
integration and the outbox effect were both built and tested; the last link — resolving the host's
connection — was missing, and its absence produced no error anywhere.

These tests assert the EFFECTIVE state: what the (fake) Google client was actually asked to do, and
what the booking row actually holds afterwards. They also pin the two ways this can go wrong
silently:

* a host with NO connected calendar must enqueue NO Google intent (benign, the self-hoster);
* a host who HAD a calendar when the booking was taken but whose connection cannot be resolved at
  drain time must FAIL LOUDLY (retry → dead-letter → visible backlog), never pass as delivered.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# The calendar-id-aware fake lives with the calendar-target tests; reused here so "which calendar
# was actually called" stays observable end to end (pytest's rootdir import mode puts the tests
# directory on sys.path, the same way the other modules in this suite share helpers).
from test_calendars_targets import FakeGoogle

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    EventType,
    ExternalCalendarLink,
    ExternalConnection,
    Outbox,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.bookings import (
    BookingEffects,
    BookingParams,
    cancel_booking,
    create_booking,
    reschedule_booking,
)
from aethercal.server.services.calendars import (
    AmbiguousCalendarTargetError,
    CalendarTargetMissingError,
    GoogleCredential,
    link_booking_calendar,
    store_google_connection,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxExecutor,
    OutboxReport,
    OutboxWork,
    drain_outbox,
    make_booking_effect_executor,
    run_google_effect,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)  # a Friday
SLOT_9 = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)  # the following Monday, 09:00 UTC
SLOT_11 = datetime(2026, 7, 13, 11, 0, tzinfo=UTC)


class _Sender:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, EventType, User]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(
        tenant_id=tenant.id,
        name="Weekdays",
        timezone="UTC",
        rules={str(day): [{"start": "08:00", "end": "18:00"}] for day in range(5)},
    )
    session.add(schedule)
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="discovery",
        title="Discovery call",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )
    session.add(event_type)
    await session.flush()
    return tenant, event_type, host


async def _connect(
    session: AsyncSession,
    tenant: Tenant,
    host: User,
    *,
    fernet: Fernet,
    account_email: str = "agency@agency.test",
) -> ExternalConnection:
    """Connect a Google account to the host, with a fresh EMPTY busy coverage window.

    The coverage stamp keeps the connected-but-unsynced calendar from making the slot unbookable
    (RF-13), so these tests isolate the write path.
    """
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email=account_email, token_json='{"token": "at"}'),
        fernet=fernet,
    )
    connection.busy_synced_from = NOW
    connection.busy_synced_to = NOW + timedelta(days=30)
    connection.busy_synced_at = NOW
    await session.flush()
    return connection


def _effects() -> BookingEffects:
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"), booking_base_url="https://book.example.com"
    )


async def _book(
    session: AsyncSession, tenant: Tenant, event_type: EventType, *, start: datetime = SLOT_9
) -> Booking:
    return await create_booking(
        session,
        tenant_id=tenant.id,
        params=BookingParams(
            event_type_id=event_type.id,
            start=start,
            guest_name="Lead",
            guest_email="lead@example.com",
            guest_timezone="UTC",
        ),
        now=NOW,
        effects=_effects(),
    )


async def _drain(
    session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    execute: OutboxExecutor,
) -> OutboxReport:
    """Commit, then drain. The outbox is a POST-COMMIT mechanism and the drain opens its OWN
    sessions — it must hold no transaction across the network call (R8) — so the intents have to be
    committed before a handler can see them."""
    await session.commit()
    return await drain_outbox(maker, now=NOW, execute=execute)


def _as_work(row: Outbox) -> OutboxWork:
    """The detached snapshot the drain hands a handler (never a live ORM row)."""
    return OutboxWork(
        id=row.id,
        tenant_id=row.tenant_id,
        booking_id=row.booking_id,
        effect=OutboxEffect(row.effect),
        dedupe_key=row.dedupe_key,
        payload=dict(row.payload),
        attempts=row.attempts,
        claimed_by="test-worker",
    )


async def _google_intents(session: AsyncSession, booking_id: uuid.UUID) -> list[Outbox]:
    return list(
        (
            await session.scalars(
                select(Outbox).where(
                    Outbox.booking_id == booking_id,
                    Outbox.effect == OutboxEffect.GOOGLE.value,
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------------------
# RF-11 — the event is created, deleted and moved in the host's calendar. For real.
# --------------------------------------------------------------------------------------


async def test_a_booking_creates_the_event_in_the_hosts_calendar(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    google = FakeGoogle()

    booking = await _book(sqlite_session, tenant, event_type)
    await _drain(
        sqlite_session,
        sqlite_maker,
        make_booking_effect_executor(
            sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
        ),
    )

    # The regression this whole cut exists for: the host's calendar now has the meeting.
    await sqlite_session.refresh(booking)
    assert google.created == [("primary", "evt-1")]
    assert booking.external_event_id == "evt-1"
    assert booking.meeting_url == "https://meet.example/evt-1"
    # And the booking remembers WHERE the event lives, so a cancel can delete the right one.
    assert booking.external_connection_id == connection.id
    assert booking.external_calendar_id == "primary"


async def test_the_event_lands_in_the_dedicated_calendar_when_one_is_designated(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """The agency credential rule: a dedicated secondary calendar, never a personal ``primary``."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    sqlite_session.add(
        ExternalCalendarLink(
            tenant_id=tenant.id,
            connection_id=connection.id,
            external_calendar_id="bookings@group.calendar.google.com",
            is_booking_target=True,
        )
    )
    await sqlite_session.flush()
    google = FakeGoogle()

    booking = await _book(sqlite_session, tenant, event_type)
    await _drain(
        sqlite_session,
        sqlite_maker,
        make_booking_effect_executor(
            sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
        ),
    )

    await sqlite_session.refresh(booking)
    assert google.created == [("bookings@group.calendar.google.com", "evt-1")]
    assert booking.external_calendar_id == "bookings@group.calendar.google.com"


async def test_cancelling_deletes_the_event_from_the_calendar_it_lives_in(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    sqlite_session.add(
        ExternalCalendarLink(
            tenant_id=tenant.id,
            connection_id=connection.id,
            external_calendar_id="dedicated@cal",
            is_booking_target=True,
        )
    )
    await sqlite_session.flush()
    google = FakeGoogle()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
    )

    booking = await _book(sqlite_session, tenant, event_type)
    await _drain(sqlite_session, sqlite_maker, execute)
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=NOW, effects=_effects()
    )
    await _drain(sqlite_session, sqlite_maker, execute)

    await sqlite_session.refresh(booking)
    assert google.deleted == [("dedicated@cal", "evt-1")]
    assert booking.status is BookingStatus.CANCELLED


async def test_rescheduling_moves_the_event(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    await _connect(sqlite_session, tenant, host, fernet=fernet)
    google = FakeGoogle()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
    )

    first = await _book(sqlite_session, tenant, event_type)
    await _drain(sqlite_session, sqlite_maker, execute)
    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=first.id,
        new_start=SLOT_11,
        now=NOW,
        effects=_effects(),
    )
    await _drain(sqlite_session, sqlite_maker, execute)

    # The old event is gone and exactly one live event remains — for the surviving booking.
    await sqlite_session.refresh(moved)
    assert google.deleted == [("primary", "evt-1")]
    assert google.created == [("primary", "evt-1"), ("primary", "evt-2")]
    assert moved.external_event_id == "evt-2"


# --------------------------------------------------------------------------------------
# The two silent-failure modes the wiring could have introduced.
# --------------------------------------------------------------------------------------


async def test_cancelling_after_the_write_target_moved_deletes_from_the_original_calendar(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """The whole reason the booking records WHERE its event lives.

    The operator re-designates the connection's booking calendar between the confirmation and the
    cancellation. Deleting from the calendar configured NOW would hit a calendar the event was never
    written to; Google would answer 404, ``_is_already_gone`` would (correctly) count that as a
    success — and the real event would sit in the host's original calendar forever while the system
    reported it deleted. The persisted target is what makes that impossible.
    """
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    await link_booking_calendar(sqlite_session, connection=connection, calendar_id="old@cal")
    google = FakeGoogle()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
    )

    booking = await _book(sqlite_session, tenant, event_type)
    await _drain(sqlite_session, sqlite_maker, execute)
    await sqlite_session.refresh(booking)
    assert google.created == [("old@cal", "evt-1")]
    assert booking.external_calendar_id == "old@cal"  # persisted with the event, in one flush

    # The operator moves the booking target to a brand-new calendar.
    await link_booking_calendar(sqlite_session, connection=connection, calendar_id="new@cal")

    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=NOW, effects=_effects()
    )
    await _drain(sqlite_session, sqlite_maker, execute)

    # The delete follows the EVENT, not the configuration: nothing is orphaned in old@cal.
    assert google.deleted == [("old@cal", "evt-1")]
    assert ("new@cal", "evt-1") not in google.deleted


async def test_rescheduling_after_the_write_target_moved_deletes_old_and_creates_new(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """Same rule for the move: the old event is removed from the calendar it LIVES in, and the new
    one is created in the calendar the host is configured for NOW."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    await link_booking_calendar(sqlite_session, connection=connection, calendar_id="old@cal")
    google = FakeGoogle()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
    )

    first = await _book(sqlite_session, tenant, event_type)
    await _drain(sqlite_session, sqlite_maker, execute)
    await link_booking_calendar(sqlite_session, connection=connection, calendar_id="new@cal")

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=first.id,
        new_start=SLOT_11,
        now=NOW,
        effects=_effects(),
    )
    await _drain(sqlite_session, sqlite_maker, execute)
    await sqlite_session.refresh(moved)

    assert google.deleted == [("old@cal", "evt-1")]
    assert google.created == [("old@cal", "evt-1"), ("new@cal", "evt-2")]
    assert moved.external_calendar_id == "new@cal"  # and the new home is recorded in its turn


async def test_a_booking_from_before_the_columns_existed_falls_back_and_says_so(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A booking predating this migration can hold an event id and NO recorded calendar. There is
    no other information to act on, so the delete falls back to the host's currently configured
    target — but it is an EXPLICIT, logged fallback, never a silent guess."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    await link_booking_calendar(sqlite_session, connection=connection, calendar_id="current@cal")
    google = FakeGoogle()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
    )

    booking = await _book(sqlite_session, tenant, event_type)
    # The legacy row: an event exists, its calendar was never recorded (the columns did not exist).
    booking.external_event_id = "legacy-evt"
    booking.external_connection_id = None
    booking.external_calendar_id = None
    await sqlite_session.flush()

    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=NOW, effects=_effects()
    )
    with caplog.at_level(logging.WARNING):
        await _drain(sqlite_session, sqlite_maker, execute)

    assert google.deleted == [("current@cal", "legacy-evt")]
    assert any("no recorded calendar" in record.message for record in caplog.records)


async def test_a_host_without_a_calendar_enqueues_no_google_intent(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The benign branch (RNF-9): the self-hoster has no Google account. Booking works, no intent
    is queued, nothing fails — and this must stay distinguishable from a FAILED resolution."""
    tenant, event_type, _host = await _seed(sqlite_session, tenant_factory)

    booking = await _book(sqlite_session, tenant, event_type)

    assert booking.status is BookingStatus.CONFIRMED
    assert await _google_intents(sqlite_session, booking.id) == []
    assert booking.external_event_id is None


async def test_a_connection_that_vanishes_before_the_drain_fails_loudly(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """The poisonous branch. The host HAD a connected calendar when the booking was taken (so the
    intent was enqueued), and by drain time it resolves to nothing. Skipping quietly would confirm
    the guest, leave the host's calendar empty, and mark the effect delivered — nobody would ever
    know. It must raise: the intent retries, then dead-letters into the visible backlog."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    google = FakeGoogle()

    booking = await _book(sqlite_session, tenant, event_type)
    intents = await _google_intents(sqlite_session, booking.id)
    assert len(intents) == 1  # the intent WAS enqueued: a calendar was expected

    connection.revoked_at = NOW  # the host disconnects Google between booking and drain
    await sqlite_session.flush()

    report = await _drain(
        sqlite_session,
        sqlite_maker,
        make_booking_effect_executor(
            sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
        ),
    )

    assert intents[0].id in report.failed  # retried, NOT silently marked delivered
    await sqlite_session.refresh(intents[0])  # the drain settled it in its own transaction
    assert intents[0].status == "failed"
    assert google.created == []


async def test_an_ambiguous_calendar_configuration_fails_loudly(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """Two connected accounts and no designated booking target: the old ``.first()`` would have
    written the client's meeting into an arbitrary one. The intent retries instead of guessing."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    await _connect(sqlite_session, tenant, host, fernet=fernet, account_email="a@agency.test")
    await _connect(sqlite_session, tenant, host, fernet=fernet, account_email="b@agency.test")
    google = FakeGoogle()

    booking = await _book(sqlite_session, tenant, event_type)
    report = await _drain(
        sqlite_session,
        sqlite_maker,
        make_booking_effect_executor(
            sessionmaker=sqlite_maker, sender=_Sender(), service_factory=lambda _c: google
        ),
    )

    intents = await _google_intents(sqlite_session, booking.id)
    assert intents[0].id in report.failed
    assert google.created == []


async def test_the_loud_errors_are_the_declared_ones(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
    fernet: Fernet,
) -> None:
    """The drain swallows exceptions into retries, so assert the EFFECT itself raises the specific
    errors — a future refactor that turns either back into a silent ``return`` fails here."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    booking = await _book(sqlite_session, tenant, event_type)
    work = _as_work((await _google_intents(sqlite_session, booking.id))[0])

    connection.revoked_at = NOW  # the host disconnects Google between booking and drain
    await sqlite_session.commit()  # the handler opens its OWN session: it only sees committed state
    with pytest.raises(CalendarTargetMissingError):
        await run_google_effect(sqlite_maker, work, NOW, service_factory=lambda _c: FakeGoogle())

    connection.revoked_at = None
    await _connect(sqlite_session, tenant, host, fernet=fernet, account_email="second@agency.test")
    await sqlite_session.commit()
    with pytest.raises(AmbiguousCalendarTargetError):
        await run_google_effect(sqlite_maker, work, NOW, service_factory=lambda _c: FakeGoogle())
