"""Offline service tests for the booking lifecycle (F1-05, RF-04/RF-07/RF-16).

Runs against the in-memory ``sqlite_session``: seed a tenant + host + weekly schedule + event type,
then drive ``create_booking`` / ``cancel_booking`` / ``reschedule_booking`` with ``effects=None`` so
no external IO happens (the booking row + the in-transaction webhook still do). These prove the
domain rules the RF-04 concurrency proof (Postgres-only, ``test_booking_concurrency.py``) then
backstops at the storage layer:

* a booking lands only on a slot ``compute_slots`` actually offers (unknown event type → not found;
  an ``unavailable`` external calendar refuses; a slot that is not on offer is rejected);
* a second booking for the same slot is refused (the partial-index/``WHERE status<>'cancelled'``
  semantics — verified offline because ``Base.metadata.create_all`` now builds the partial index on
  SQLite too);
* cancelling frees the slot (a later booking on it succeeds) and is idempotent;
* rescheduling opens a new confirmed booking linked by ``rescheduled_from_id`` and cancels the old;
* every lifecycle event is durably queued as a JSON-serializable webhook in the same transaction;
* cross-tenant isolation holds on every path.

The ``effects`` tests drive the bundle with an in-memory recording ``EmailSender`` — still no
network — to prove the durable side-effects are wired: the guest tokens are minted in-txn, the email
is ENQUEUED to the transactional outbox (not sent inline pre-commit), and draining the outbox runs
the effect once (with retry + idempotent re-drain). The persisted iCal SEQUENCE increments across
the lifecycle (F1-08).

The drain runs POST-COMMIT and owns its own transaction boundaries (R8: it claims a row, commits,
does the network I/O with nothing open, then settles), so these tests COMMIT the working session and
hand the drain a ``sessionmaker`` on the same in-memory database — see the ``_drain`` helper. There
is no reminder runner: RF-10 is now a workflow rule materialised into this same outbox, so there is
exactly one thing that can decide to remind a guest."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from icalendar import Calendar
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    EventType,
    GuestToken,
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
    AvailabilityUnavailableError,
    BookingEffects,
    BookingNotActiveError,
    BookingNotFoundError,
    BookingParams,
    DayFullError,
    EventTypeInactiveError,
    EventTypeNotFoundError,
    SlotUnavailableError,
    _mint_guest_links,
    cancel_booking,
    create_booking,
    get_booking,
    list_bookings,
    reschedule_booking,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.event_types import create_event_type, deactivate_event_type
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxExecutor,
    OutboxReport,
    OutboxWork,
    backoff_delay,
    drain_outbox,
    email_dedupe_key,
    make_booking_effect_executor,
    run_google_effect,
)
from aethercal.server.services.tenant_senders import TenantSenders


def _pools(maker: async_sessionmaker[AsyncSession]) -> WorkerPools:
    """Both of the drain's pools over ONE offline sessionmaker.

    ``drain_outbox`` takes :class:`WorkerPools` now, not a sessionmaker, because on PostgreSQL it
    needs TWO connections: a ``BYPASSRLS`` one to find work whose business it cannot know until it
    has read the row (``select_due``, ``recover_expired_leases``, and — the one nearly missed —
    ``claim_one``, an UPDATE on a row whose ``tenant_id`` is only knowable by reading it), and the
    app role to EXECUTE each item under row-level security, bound to that item's own business.

    SQLite has neither roles nor RLS, so the two collapse back into one here and the drain behaves
    exactly as it always did. ``tests/test_bypass_belt.py`` asserts this constructor is never
    reached from the shipped source.
    """
    return WorkerPools.for_offline_tests(maker)


_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}

# 2026-07-06 is a Monday; midnight before it opens leaves every weekday slot bookable (notice=0).
_MON = date(2026, 7, 6)
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)
_SLOT_9 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_SLOT_11 = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
_OFF_HOURS = datetime(2026, 7, 6, 3, 0, tzinfo=UTC)  # 03:00 is outside the 09:00-17:00 window.
_HALF_HOUR = timedelta(minutes=30)


async def _first_user(session: AsyncSession, tenant: Tenant) -> User:
    return (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()


async def _schedule(session: AsyncSession, tenant: Tenant) -> Schedule:
    row = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_WEEKLY_9_TO_5)
    session.add(row)
    await session.flush()
    return row


async def _event_type(  # noqa: PLR0913 - a test factory: every field is one knob a test turns
    session: AsyncSession,
    tenant: Tenant,
    host: User,
    schedule: Schedule,
    *,
    slug: str = "intro",
    max_per_day: int | None = None,
) -> EventType:
    data = EventTypeCreate(
        host_id=host.id,
        schedule_id=schedule.id,
        slug=slug,
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
        max_per_day=max_per_day,
    )
    return await create_event_type(session, tenant_id=tenant.id, data=data)


async def _seed(
    session: AsyncSession, tenant_factory: Any, *, max_per_day: int | None = None
) -> tuple[Tenant, EventType]:
    tenant = await tenant_factory(session)
    host = await _first_user(session, tenant)
    schedule = await _schedule(session, tenant)
    event_type = await _event_type(session, tenant, host, schedule, max_per_day=max_per_day)
    return tenant, event_type


async def _subscribe_all(session: AsyncSession, tenant: Tenant) -> Webhook:
    row = Webhook(
        tenant_id=tenant.id,
        url="https://example.com/hook",
        secret=b"test-secret",
        events=["booking.created", "booking.cancelled", "booking.rescheduled"],
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


def _params(event_type_id: uuid.UUID, start: datetime, **over: Any) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name=over.get("guest_name", "Ada Lovelace"),
        guest_email=over.get("guest_email", "ada@example.com"),
        guest_timezone=over.get("guest_timezone", "UTC"),
        answers=over.get("answers"),
        locale=over.get("locale", "es"),
        guest_phone=over.get("guest_phone"),
        guest_phone_consent=over.get("guest_phone_consent", False),
    )


async def _deliveries(session: AsyncSession, event: str) -> list[WebhookDelivery]:
    return list(
        (await session.scalars(select(WebhookDelivery).where(WebhookDelivery.event == event))).all()
    )


async def _outbox_rows(
    session: AsyncSession, *, booking_id: uuid.UUID | None = None
) -> list[Outbox]:
    stmt = select(Outbox)
    if booking_id is not None:
        stmt = stmt.where(Outbox.booking_id == booking_id)
    # populate_existing: the drain settles these rows in its OWN sessions, so an instance already in
    # this session's identity map would otherwise keep its stale pre-drain state.
    stmt = stmt.order_by(Outbox.created_at).execution_options(populate_existing=True)
    return list((await session.scalars(stmt)).all())


def _ics_seq_and_uid(message: EmailMessage) -> tuple[int, str]:
    """Parse the SEQUENCE and UID out of a composed email's ``text/calendar`` invite."""
    for part in message.walk():
        if part.get_content_type() == "text/calendar":
            content = part.get_content()
            text = content if isinstance(content, str) else content.decode("utf-8")
            vevent: Any = Calendar.from_ical(text).walk("VEVENT")[0]
            return int(vevent["SEQUENCE"]), str(vevent["UID"])
    raise AssertionError("no text/calendar part found in the message")


def _email_body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain",))
    assert part is not None
    content = part.get_content()
    assert isinstance(content, str)
    return content


async def _active_count(session: AsyncSession, event_type: EventType) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.event_type_id == event_type.id,
                Booking.status != BookingStatus.CANCELLED,
            )
        )
    ) or 0


class _RecordingSender:
    """An in-memory :class:`EmailSender` that records what it was asked to deliver (no network)."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class _FailingSender:
    """An :class:`EmailSender` that always fails — drives the best-effort email contract."""

    async def send(self, message: EmailMessage) -> None:
        raise RuntimeError("smtp down")


class _FakeExecute:
    def __init__(self, result: Any, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def execute(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._result


class _FakeEvents:
    """Records the Google events an outbox Google effect creates and deletes (no network).

    ``fail_first`` makes the first N inserts' ``.execute()`` raise (a transient Google outage), so a
    created event is only recorded on a call that actually succeeds."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._fail_first = fail_first
        self._calls = 0
        self._n = 0

    def insert(
        self, *, calendarId: str, body: Any, conferenceDataVersion: int, sendUpdates: str
    ) -> _FakeExecute:
        self._calls += 1
        if self._calls <= self._fail_first:
            return _FakeExecute(None, RuntimeError("google transiently unavailable"))
        self._n += 1
        event_id = f"evt-{self._n}"
        self.created.append(event_id)
        return _FakeExecute({"id": event_id, "hangoutLink": f"https://meet.example/{event_id}"})

    def delete(self, *, calendarId: str, eventId: str, sendUpdates: str) -> _FakeExecute:
        self.deleted.append(eventId)
        return _FakeExecute(None)


class _FakeGoogle:
    def __init__(self, *, fail_first: int = 0) -> None:
        self.events_obj = _FakeEvents(fail_first=fail_first)

    def events(self) -> _FakeEvents:
        return self.events_obj


async def _google_effects(session: AsyncSession, tenant: Tenant) -> BookingEffects:
    """CONNECT a calendar to the host, so the booking service enqueues the Google sync intents.

    The connection is a fact in the DATABASE, not a field handed to the service. It used to be the
    latter (``BookingEffects.connection``), which is how the sync could be "tested" while production
    — which had no way to populate that field — never synced anything at all. The test now sets up
    the state production actually has, and the service resolves the host's calendar itself.
    """
    host = await _first_user(session, tenant)
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json='{"token": "at"}'),
        fernet=Fernet(derive_fernet_key("test-app-secret")),
    )
    # Stamp a fresh, wide busy-coverage window (no busy blocks) so the connected calendar reads as
    # FRESH-empty and the slot is still offered — otherwise a connected-but-uncovered calendar makes
    # slot validation refuse the booking (RF-13). This isolates the test to the Google outbox path.
    connection.busy_synced_from = datetime(2026, 7, 1, tzinfo=UTC)
    connection.busy_synced_to = datetime(2026, 7, 15, tzinfo=UTC)
    connection.busy_synced_at = _BEFORE
    await session.flush()
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
    )


# --------------------------------------------------------------------------------------
# create_booking
# --------------------------------------------------------------------------------------


async def test_create_on_offered_slot_confirms_and_queues_created_webhook(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_all(sqlite_session, tenant)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
    )

    assert booking.status == BookingStatus.CONFIRMED
    assert booking.start_at == _SLOT_9
    assert booking.end_at == _SLOT_9 + _HALF_HOUR
    assert booking.guest_email == "ada@example.com"

    created = await _deliveries(sqlite_session, "booking.created")
    assert len(created) == 1  # queued in the SAME transaction as the booking
    json.dumps(created[0].payload)  # the envelope's data must be JSON-serializable
    assert created[0].payload["data"]["id"] == str(booking.id)


# --------------------------------------------------------------------------------------
# Guest phone + consent (RF-24). These assert the EFFECTIVE state — the row re-read from the
# database — never the apparent one. A consent the service accepts and does not persist is the
# exact silent no-op this batch exists to kill: the form submits, nothing errors, and the column
# the outbox gate reads is still NULL.
# --------------------------------------------------------------------------------------


async def _reread(session: AsyncSession, booking_id: uuid.UUID) -> Booking:
    """Re-read the booking from storage (never the identity-mapped instance we just wrote)."""
    row = await session.scalar(
        select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def test_create_with_consent_seals_the_consent_column(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(
            event_type.id, _SLOT_9, guest_phone="+13054131728", guest_phone_consent=True
        ),
        now=_BEFORE,
    )

    stored = await _reread(sqlite_session, booking.id)
    assert stored.guest_phone == "+13054131728"
    # The stamp is the SERVER's clock, not a client-supplied timestamp — it is evidence.
    # SQLite has no tz-aware type and hands the instant back naive; Postgres (the real backend,
    # ``DateTime(timezone=True)``) returns it aware — `test_booking_page_consent_pg.py` asserts it
    # there. Same convention as `no_show_at` in `test_booking_state_machine.py`.
    assert stored.guest_phone_consent_at is not None
    assert stored.guest_phone_consent_at.replace(tzinfo=UTC) == _BEFORE


async def test_create_without_consent_leaves_the_consent_column_null(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A number typed with the box left unticked: held, but never consented to. The gate refuses it.

    This is the state the outbox's ``_require_phone_consent`` exists to skip — so it must stay
    REACHABLE, not be normalized away into a stamped consent nobody actually gave.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(
            event_type.id, _SLOT_9, guest_phone="+13054131728", guest_phone_consent=False
        ),
        now=_BEFORE,
    )

    stored = await _reread(sqlite_session, booking.id)
    assert stored.guest_phone == "+13054131728"
    assert stored.guest_phone_consent_at is None


async def test_create_without_a_phone_books_normally(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Booking without a phone must always work — the field is optional, and so is the consent."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    stored = await _reread(sqlite_session, booking.id)
    assert stored.status == BookingStatus.CONFIRMED
    assert stored.guest_phone is None
    assert stored.guest_phone_consent_at is None


async def test_consent_is_never_stamped_without_a_number(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Consent with no number consents to nothing: never stamp a column that proves nothing.

    The schema layer rejects this payload outright (422). The service is the second belt: a caller
    that builds ``BookingParams`` directly (the admin does) must not be able to mint a consent
    stamp for a booking that has no phone to message.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9, guest_phone=None, guest_phone_consent=True),
        now=_BEFORE,
    )

    stored = await _reread(sqlite_session, booking.id)
    assert stored.guest_phone is None
    assert stored.guest_phone_consent_at is None


async def test_second_booking_on_the_same_slot_is_refused(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    with pytest.raises(SlotUnavailableError):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(event_type.id, _SLOT_9, guest_email="bob@example.com"),
            now=_BEFORE,
        )

    assert await _active_count(sqlite_session, event_type) == 1  # exactly one active booking


async def test_create_on_a_slot_not_on_offer_is_refused(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    with pytest.raises(SlotUnavailableError):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(event_type.id, _OFF_HOURS),
            now=_BEFORE,
        )


async def test_create_for_unknown_event_type_raises_not_found(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant = await tenant_factory(sqlite_session)

    with pytest.raises(EventTypeNotFoundError):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(uuid.uuid4(), _SLOT_9),
            now=_BEFORE,
        )


async def test_create_refuses_when_external_calendar_is_unavailable(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    host = await _first_user(sqlite_session, tenant)
    # A connected but unreadable calendar with no covering cache → read_busy returns UNAVAILABLE, so
    # a booking is refused rather than risk a double-booking against an unknown calendar (RF-13).
    fernet = Fernet(derive_fernet_key("test-app-secret"))
    await store_google_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json="{}"),
        fernet=fernet,
    )
    await sqlite_session.flush()

    with pytest.raises(AvailabilityUnavailableError):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(event_type.id, _SLOT_9),
            now=_BEFORE,
        )


# --------------------------------------------------------------------------------------
# cancel_booking
# --------------------------------------------------------------------------------------


async def test_cancel_marks_cancelled_frees_the_slot_and_queues_webhook(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_all(sqlite_session, tenant)
    first = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    cancelled = await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=first.id, now=_BEFORE
    )
    assert cancelled.status == BookingStatus.CANCELLED
    assert cancelled.cancelled_at == _BEFORE
    assert len(await _deliveries(sqlite_session, "booking.cancelled")) == 1

    # The freed slot is bookable again — proving the partial-index/WHERE status<>'cancelled' rule.
    second = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9, guest_email="bob@example.com"),
        now=_BEFORE,
    )
    assert second.id != first.id
    assert second.status == BookingStatus.CONFIRMED


async def test_cancel_is_idempotent(sqlite_session: AsyncSession, tenant_factory: Any) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_all(sqlite_session, tenant)
    first = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    await cancel_booking(sqlite_session, tenant_id=tenant.id, booking_id=first.id, now=_BEFORE)
    again = await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=first.id, now=_BEFORE
    )

    assert again.status == BookingStatus.CANCELLED
    # A second cancel is a no-op: it neither errors nor queues a duplicate webhook.
    assert len(await _deliveries(sqlite_session, "booking.cancelled")) == 1


async def test_cancel_unknown_booking_raises_not_found(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant = await tenant_factory(sqlite_session)

    with pytest.raises(BookingNotFoundError):
        await cancel_booking(
            sqlite_session, tenant_id=tenant.id, booking_id=uuid.uuid4(), now=_BEFORE
        )


# --------------------------------------------------------------------------------------
# reschedule_booking
# --------------------------------------------------------------------------------------


async def test_reschedule_opens_new_booking_and_cancels_the_old_linked(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    await _subscribe_all(sqlite_session, tenant)
    original = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9, guest_name="Ada", answers={"topic": "roadmap"}),
        now=_BEFORE,
    )

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=_BEFORE,
    )

    assert moved.id != original.id
    assert moved.status == BookingStatus.CONFIRMED
    assert moved.start_at == _SLOT_11
    assert moved.end_at == _SLOT_11 + _HALF_HOUR
    assert moved.rescheduled_from_id == original.id
    assert moved.guest_name == "Ada"  # guest fields carry over
    assert moved.answers == {"topic": "roadmap"}

    refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=original.id)
    assert refreshed is not None
    assert refreshed.status == BookingStatus.CANCELLED

    rescheduled = await _deliveries(sqlite_session, "booking.rescheduled")
    assert len(rescheduled) == 1
    json.dumps(rescheduled[0].payload)
    assert await _active_count(sqlite_session, event_type) == 1  # old freed, new active


async def test_reschedule_to_a_taken_slot_is_refused_and_leaves_original_intact(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_11, guest_email="other@example.com"),
        now=_BEFORE,
    )

    with pytest.raises(SlotUnavailableError):
        await reschedule_booking(
            sqlite_session,
            tenant_id=tenant.id,
            booking_id=original.id,
            new_start=_SLOT_11,
            now=_BEFORE,
        )

    still = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=original.id)
    assert still is not None
    assert (
        still.status == BookingStatus.CONFIRMED
    )  # a refused reschedule never touches the original


async def test_reschedule_a_cancelled_booking_is_refused(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await cancel_booking(sqlite_session, tenant_id=tenant.id, booking_id=original.id, now=_BEFORE)

    with pytest.raises(BookingNotActiveError):
        await reschedule_booking(
            sqlite_session,
            tenant_id=tenant.id,
            booking_id=original.id,
            new_start=_SLOT_11,
            now=_BEFORE,
        )


async def test_reschedule_carries_the_guests_phone_and_its_consent_stamp(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The successor is the SAME guest's SAME appointment, so it keeps their phone — and the stamp
    that says they agreed to be messaged on it.

    Dropping either one silently unsubscribes the guest from every phone channel: the WhatsApp/SMS
    step reads ``guest_phone`` (skip ``no-phone``) and then ``guest_phone_consent_at`` (skip
    ``no-phone-consent``), so a reschedule would quietly end the reminders — no error, no log line
    anybody reads, and the guest simply never hears from us again.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(
            event_type.id, _SLOT_9, guest_phone="+13055551234", guest_phone_consent=True
        ),
        now=_BEFORE,
    )
    assert original.guest_phone == "+13055551234"
    assert original.guest_phone_consent_at == _BEFORE

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=_BEFORE,
    )

    assert moved.guest_phone == original.guest_phone
    assert moved.guest_phone_consent_at == original.guest_phone_consent_at


def _sample_guest_value(column: sa.Column[Any]) -> Any:
    """A distinctive non-null value for a guest column, chosen from its TYPE — never a hand-list.

    This is what makes the inheritance test below structural instead of decorative. A guest column
    left unpopulated would compare ``None == None`` on both rows and pass while proving nothing, so
    a column whose type this does not know raises rather than being skipped: adding
    ``guest_something`` of a new type fails HERE, loudly, and the author has to say what it means.
    """
    if isinstance(column.type, sa.String):  # covers Text (a String subclass)
        value = f"sample-{column.name}"
        return value[: column.type.length] if column.type.length else value
    if isinstance(column.type, sa.DateTime):
        return datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    if isinstance(column.type, sa.Boolean):
        return True
    if isinstance(column.type, sa.Integer):
        return 7
    raise AssertionError(
        f"{column.name}: no sample value for {column.type!r}. Teach this helper the type — do not "
        "skip the column, or the reschedule-inheritance assertion silently stops covering it."
    )


async def test_reschedule_inherits_every_guest_column_the_model_declares(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The successor inherits EVERY ``guest_*`` column ``Booking`` declares — read off the metadata,
    not off a list in this file.

    The bug this pins is not "``guest_phone`` was forgotten". It is that the successor was built by
    COPYING FIELDS BY HAND, so the set of inherited fields and the set of declared fields were two
    lists kept in agreement by memory alone. They drifted the moment RF-24 added a column, and
    nothing failed: the guest simply stopped getting messages.

    So the assertion is derived the way ``test_models_metadata.py`` derives its invariants — from
    ``Booking.__table__``. Add a guest column tomorrow and this test starts covering it on its own;
    fail to carry it and this test, not a guest's missing reminder, is what tells you.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    columns = [col for col in Booking.__table__.columns if col.name.startswith("guest_")]
    assert columns, "no guest_* columns found — the derivation is broken, not the model"
    for column in columns:
        setattr(original, column.name, _sample_guest_value(column))
    await sqlite_session.flush()
    await sqlite_session.refresh(original)

    # Compare the successor against what the PREDECESSOR actually holds, not against the literals
    # above: "inherits" means the two rows agree, and SQLite drops tzinfo on round-trip so the
    # literals are not what came back. The not-None guard is what keeps this honest — an unpopulated
    # column would compare None == None and pass while covering nothing.
    stored = {column.name: getattr(original, column.name) for column in columns}
    assert all(value is not None for value in stored.values()), (
        f"every guest column must hold a real value before the move, else the assertion below is "
        f"vacuous: {stored}"
    )

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=_BEFORE,
    )

    assert {name: getattr(moved, name) for name in stored} == stored


# --------------------------------------------------------------------------------------
# read paths + isolation
# --------------------------------------------------------------------------------------


async def test_get_and_list_bookings_filter_by_status(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    first = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_11, guest_email="two@example.com"),
        now=_BEFORE,
    )

    found = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=first.id)
    assert found is not None and found.id == first.id
    assert await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=uuid.uuid4()) is None

    assert len(await list_bookings(sqlite_session, tenant_id=tenant.id)) == 2
    assert (
        len(
            await list_bookings(sqlite_session, tenant_id=tenant.id, status=BookingStatus.CONFIRMED)
        )
        == 2
    )
    await cancel_booking(sqlite_session, tenant_id=tenant.id, booking_id=first.id, now=_BEFORE)
    cancelled = await list_bookings(
        sqlite_session, tenant_id=tenant.id, status=BookingStatus.CANCELLED
    )
    assert len(cancelled) == 1 and cancelled[0].id == first.id


async def test_cross_tenant_isolation_on_every_path(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    owner, event_type = await _seed(sqlite_session, tenant_factory)
    intruder = await tenant_factory(sqlite_session, slug="intruder")
    booking = await create_booking(
        sqlite_session, tenant_id=owner.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    # The intruder cannot see the owner's event type (create), booking (get), or cancel it.
    with pytest.raises(EventTypeNotFoundError):
        await create_booking(
            sqlite_session,
            tenant_id=intruder.id,
            params=_params(event_type.id, _SLOT_11),
            now=_BEFORE,
        )
    assert await get_booking(sqlite_session, tenant_id=intruder.id, booking_id=booking.id) is None
    with pytest.raises(BookingNotFoundError):
        await cancel_booking(
            sqlite_session, tenant_id=intruder.id, booking_id=booking.id, now=_BEFORE
        )


# --------------------------------------------------------------------------------------
# effects bundle (in-memory fakes — still no network)
# --------------------------------------------------------------------------------------


def _effects() -> BookingEffects:
    # The email intent is enqueued unconditionally now (the drain owns the live sender), so the
    # bundle no longer carries a sender — tests hand a recording sender to the drain executor.
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
    )


async def _drain(
    session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    execute: OutboxExecutor,
    now: datetime = _BEFORE,
) -> OutboxReport:
    """Commit, then drain — the outbox is a POST-COMMIT mechanism, and the drain opens its own
    sessions (it must not hold a transaction across the network I/O, R8). Afterwards the working
    session's identity map is expired, so the test re-reads what the drain committed."""
    await session.commit()
    return await drain_outbox(_pools(maker), now=now, execute=execute)


async def test_create_mints_tokens_and_enqueues_the_email_intent(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )

    # Two signed guest tokens (cancel + reschedule) are minted and stored (hashed) in the same txn.
    tokens = list(
        (
            await sqlite_session.scalars(
                select(GuestToken).where(GuestToken.booking_id == booking.id)
            )
        ).all()
    )
    assert {t.purpose for t in tokens} == {"cancel", "reschedule"}
    # The confirmation email is NOT sent inline — it is ENQUEUED as a durable outbox intent in the
    # booking's transaction (drained post-commit), so it can never fire for a rolled-back booking.
    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    assert len(rows) == 1
    intent = rows[0]
    assert intent.effect == OutboxEffect.EMAIL.value
    assert intent.dedupe_key == email_dedupe_key(NotificationKind.CONFIRMATION)
    assert intent.status == "pending"
    assert intent.payload["cancel_url"].startswith("https://book.example.com/cancel?token=")
    reschedule_url = intent.payload["reschedule_url"]
    assert reschedule_url.startswith("https://book.example.com/reschedule?token=")
    # Nothing is scheduled inline any more: RF-10's reminder is a workflow rule that materialises
    # into this same outbox. Google is unwired here → no sync intent.
    assert booking.external_event_id is None
    assert booking.sequence == 0  # a fresh booking starts at the confirmation sequence


async def test_email_intent_is_enqueued_and_retryable_even_without_a_configured_sender(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Durability: the confirmation is domain-required, so its intent is ENQUEUED even when SMTP is
    absent at booking time. The drain then FAILS RETRYABLY (rather than dropping it), so the notice
    goes out once SMTP is configured — never silently lost."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),  # no sender wired in the bundle — the drain owns the live sender
    )

    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    assert [r.dedupe_key for r in rows] == [email_dedupe_key(NotificationKind.CONFIRMATION)]

    # Draining with no sender configured fails the intent retryably (not delivered, not dropped).
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=None),
        service_factory=None,
    )
    report = await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)
    assert report.failed == [rows[0].id]
    settled = await _outbox_rows(sqlite_session, booking_id=booking.id)
    assert settled[0].status == "failed"


async def test_draining_the_outbox_sends_the_confirmation_once_and_re_drain_is_idempotent(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )

    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=None,
    )
    report = await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    assert report.delivered == [(await _outbox_rows(sqlite_session, booking_id=booking.id))[0].id]
    assert len(sender.sent) == 1
    seq, uid = _ics_seq_and_uid(sender.sent[0])
    assert seq == 0 and uid == booking.ical_uid

    # A re-drain executes nothing (the intent is delivered) and never mails the guest twice.
    again = await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)
    assert again.attempted == 0
    assert len(sender.sent) == 1


async def test_a_failing_send_marks_the_intent_for_retry_without_touching_the_booking(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )

    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=_FailingSender()),
        service_factory=None,
    )
    report = await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # The send raised → the intent is parked failed for a backoff retry; the booking is untouched.
    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    assert report.failed == [rows[0].id]
    assert rows[0].status == "failed"
    assert rows[0].next_retry_at is not None
    persisted = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert persisted is not None and persisted.status == BookingStatus.CONFIRMED


async def test_cancel_enqueues_a_cancellation_email_intent(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )
    await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        now=_BEFORE,
        effects=_effects(),
    )

    keys = {row.dedupe_key for row in await _outbox_rows(sqlite_session, booking_id=booking.id)}
    assert email_dedupe_key(NotificationKind.CANCELLATION) in keys
    assert sender.sent == []  # still nothing sent inline — both intents await the drain


async def test_confirmation_and_cancellation_drained_together_only_sends_the_cancellation(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """When the confirmation and cancellation emails drain in the SAME pass (create then cancel
    before any drain), the confirmation is already STALE — the booking was cancelled — so it is
    DISCARDED and only the cancellation is sent (sequence 1). The guest never receives a "confirmed"
    for a booking already cancelled, regardless of the internal drain order.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )
    await cancel_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        now=_BEFORE,
        effects=_effects(),
    )

    # Drain BOTH email intents in one pass.
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=None,
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # Exactly one email — the cancellation (sequence 1); the stale confirmation was dropped.
    assert len(sender.sent) == 1
    seq, uid = _ics_seq_and_uid(sender.sent[0])
    assert seq == 1 and uid == booking.ical_uid
    assert "cancelada" in str(sender.sent[0]["Subject"]).lower()
    # Both intents are consumed (delivered) — the discarded one is not left retrying.
    email_rows = [
        r
        for r in await _outbox_rows(sqlite_session, booking_id=booking.id)
        if r.effect == OutboxEffect.EMAIL.value
    ]
    assert {r.status for r in email_rows} == {"delivered"}


async def test_a_confirmation_retried_after_cancellation_is_discarded_as_stale(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The at-least-once hazard the guard closes: a confirmation email that kept FAILING and finally
    retries AFTER the booking was cancelled must be DISCARDED (no "confirmed" after "cancelled"),
    while the cancellation is still sent — the notifications keep causal order under retries."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )  # email:confirmation enqueued

    # Pass 1: the confirmation send FAILS (SMTP down) → parked failed for a backoff retry.
    failing = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=_FailingSender()),
        service_factory=None,
    )
    await _drain(sqlite_session, sqlite_maker, failing, now=_BEFORE)

    # The booking is CANCELLED before the confirmation ever succeeds (cancellation email enqueued).
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_BEFORE, effects=_effects()
    )

    # Pass 2 (past the confirmation's retry): a WORKING sender drains both due intents.
    sender = _RecordingSender()
    working = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=None,
    )
    await _drain(sqlite_session, sqlite_maker, working, now=_BEFORE + backoff_delay(1))

    # The retried confirmation is discarded as stale; only the cancellation reaches the guest.
    subjects = [str(m["Subject"]).lower() for m in sender.sent]
    assert not any("confirmada" in s for s in subjects)  # no "confirmed" after "cancelled"
    assert any("cancelada" in s for s in subjects)  # the cancellation is delivered


async def test_a_reschedule_email_retried_after_a_further_reschedule_is_discarded_as_stale(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Same guard for reschedules: a reschedule notice that kept failing and retries AFTER the
    booking was rescheduled AGAIN is discarded — the guest gets only the LATEST reschedule, never an
    outdated one for a slot already replaced."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=_effects(),
    )  # reschedule email for b2 enqueued

    # Pass 1: b2's reschedule send FAILS (b1's now-stale confirmation is silently discarded).
    failing = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=_FailingSender()),
        service_factory=None,
    )
    await _drain(sqlite_session, sqlite_maker, failing, now=_BEFORE)

    # b2 is rescheduled AGAIN to b3 before its own reschedule notice ever succeeds.
    _SLOT_13 = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)
    b3 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b2.id,
        new_start=_SLOT_13,
        now=_BEFORE,
        effects=_effects(),
    )

    # Pass 2 (past the retry): the stale b2 reschedule is discarded; only b3's reschedule is sent.
    sender = _RecordingSender()
    working = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=None,
    )
    await _drain(sqlite_session, sqlite_maker, working, now=_BEFORE + backoff_delay(1))

    assert len(sender.sent) == 1
    seq, uid = _ics_seq_and_uid(sender.sent[0])
    assert seq == b3.sequence == 2  # the latest reschedule
    assert uid == b1.ical_uid  # the whole chain shares one UID
    assert "reprogramada" in str(sender.sent[0]["Subject"]).lower()


async def test_persisted_sequence_strictly_increases_across_reschedules_and_cancel(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """F1-08: confirmation starts at 0, successive reschedules strictly increase, and a cancellation
    uses the next value — proven on the actual emitted ``.ics`` SEQUENCE, drained after each step.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=None,
    )

    async def _pass() -> None:
        await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )
    await _pass()
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=_effects(),
    )
    await _pass()
    _SLOT_13 = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)
    b3 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b2.id,
        new_start=_SLOT_13,
        now=_BEFORE,
        effects=_effects(),
    )
    await _pass()
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=b3.id, now=_BEFORE, effects=_effects()
    )
    await _pass()

    # The persisted counter advanced on every .ics-emitting mutation: the confirmation stays at 0,
    # each reschedule carries its predecessor + 1, and the cancellation bumps b3 one more (2 → 3).
    assert b1.sequence == 0  # a reschedule cancels the old row without bumping its sequence
    assert b2.sequence == 1
    assert b3.sequence == 3  # created at 2 by the reschedule, then bumped to 3 by the cancellation

    # The whole chain shares ONE stable UID (the reschedule successors inherit b1's), and the
    # emitted .ics sequences strictly increase over that single UID: confirmation 0, reschedules 1 →
    # 2, cancellation 3 — exactly what RFC 5545 requires for a client to honor every update.
    emitted = [_ics_seq_and_uid(msg) for msg in sender.sent]
    assert b1.ical_uid == b2.ical_uid == b3.ical_uid  # inherited across the reschedule chain
    assert {uid for _seq, uid in emitted} == {b1.ical_uid}  # every email addresses the same event
    sequences = [seq for seq, _uid in emitted]
    assert sequences == [0, 1, 2, 3]  # confirmation 0 < reschedule 1 < reschedule 2 < cancel 3


async def test_create_then_cancel_before_drain_leaves_no_orphaned_google_event(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Both Google intents (upsert + delete) are enqueued before either drains. The drain must not
    leave a live event for the cancelled booking, independent of the order it processes them — the
    create reconciles to the booking's CURRENT (cancelled) state and skips, so nothing is orphaned.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_BEFORE, effects=effects
    )

    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=lambda _c: google,
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # The create reconciled to "cancelled → no event" and skipped, so nothing was created and none
    # dangles; the booking never points at a live Google event.
    assert google.events_obj.created == []
    assert set(google.events_obj.created) == set(google.events_obj.deleted)
    refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert refreshed is not None and refreshed.external_event_id is None


async def test_create_then_reschedule_before_drain_yields_one_event_for_the_survivor(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Create + reschedule enqueue two Google intents before either drains. Draining must leave
    exactly ONE event — for the surviving (rescheduled) booking — never two, in any drain order."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=effects,
    )

    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=lambda _c: google,
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # Exactly one live event, owned by the surviving booking b2; b1 (cancelled by the reschedule)
    # never keeps a live event.
    live = set(google.events_obj.created) - set(google.events_obj.deleted)
    assert len(live) == 1
    b2_refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=b2.id)
    assert b2_refreshed is not None and b2_refreshed.external_event_id in live
    b1_refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=b1.id)
    assert b1_refreshed is not None and b1_refreshed.external_event_id is None


async def test_cancelling_a_not_yet_synced_reschedule_deletes_the_original_event(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Create + drain (event exists), reschedule WITHOUT draining, then cancel the successor and
    drain: the cancellation must delete the ORIGINAL event (resolved by walking the reschedule
    chain), leaving exactly one delete and no orphan — the successor never got an id of its own."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=lambda _c: google,
    )

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)  # E1 now exists for b1
    await sqlite_session.refresh(b1)
    assert google.events_obj.created == ["evt-1"]
    assert b1.external_event_id == "evt-1"

    # Reschedule WITHOUT draining (b2 never gets its own event id), then cancel b2.
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=effects,
    )
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=b2.id, now=_BEFORE, effects=effects
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # Exactly one create (the original) and exactly one delete OF THAT SAME event — no orphan.
    assert google.events_obj.created == ["evt-1"]
    assert google.events_obj.deleted == ["evt-1"]


async def _google_row(session: AsyncSession, booking_id: uuid.UUID) -> Outbox:
    rows = await _outbox_rows(session, booking_id=booking_id)
    return next(r for r in rows if r.effect == OutboxEffect.GOOGLE.value)


def _as_work(row: Outbox) -> OutboxWork:
    """The detached snapshot the drain hands a handler (never a live ORM row — see OutboxWork)."""
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


async def test_reschedule_drained_before_the_original_upsert_never_recreates_the_old_event(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Inverted order (two workers): the successor's RESCHEDULE runs BEFORE the original's UPSERT
    (neither drained yet). The replaced predecessor's UPSERT must be SKIPPED — it is no longer the
    chain's current booking — so exactly ONE event exists (for the successor) and the old one is
    never recreated. Executed as two explicit steps to pin the inverted causal order."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )  # google:upsert enqueued, NOT drained
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=effects,
    )  # google:reschedule enqueued, NOT drained
    upsert = _as_work(await _google_row(sqlite_session, b1.id))
    reschedule = _as_work(await _google_row(sqlite_session, b2.id))
    # The handlers open their OWN sessions (they must run their network I/O with no transaction
    # held, R8), so the enqueued intents have to be committed before they can be seen.
    await sqlite_session.commit()

    def factory(_conn: Any) -> _FakeGoogle:
        return google

    # RESCHEDULE first: b2 is the chain's current booking → it creates the event.
    await run_google_effect(sqlite_maker, reschedule, _BEFORE, service_factory=factory)
    # Then the original UPSERT: b1 was replaced → skipped, no second/old event ever created.
    await run_google_effect(sqlite_maker, upsert, _BEFORE, service_factory=factory)

    await sqlite_session.refresh(b1)
    await sqlite_session.refresh(b2)
    assert google.events_obj.created == ["evt-1"]  # exactly one event, old one never recreated
    assert b2.external_event_id == "evt-1"
    assert b1.external_event_id is None


async def test_google_sync_runs_before_the_email_so_the_notice_carries_the_meet_link(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The Google-sync and confirmation-email intents share a created_at (same txn). The drain runs
    Google FIRST (deterministic priority), so it writes the Meet link onto the booking before the
    email is composed — the confirmation carries the link instead of racing ahead of it."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )

    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=lambda _c: google,
    )
    await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)

    # Google ran first and set the Meet link; the confirmation email (drained after) carries it.
    await sqlite_session.refresh(booking)
    assert booking.meeting_url == "https://meet.example/evt-1"
    assert len(sender.sent) == 1
    assert booking.meeting_url in _email_body(sender.sent[0])


async def test_email_defers_without_consuming_attempts_until_google_delivers_the_link(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A TRANSIENT Google failure must not let the confirmation go out without the Meet link. While
    the sync is still pending/failed the email DEFERS (no attempt used), then sends WITH the link
    once Google succeeds on retry — the dependency, not just same-pass ordering, is enforced."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle(fail_first=1)  # the first Google insert fails; its retry succeeds
    effects = await _google_effects(sqlite_session, tenant)
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )
    execute = make_booking_effect_executor(
        sessionmaker=sqlite_maker,
        resolve_senders=TenantSenders.for_offline_tests(email=sender),
        service_factory=lambda _c: google,
    )
    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    email = next(r for r in rows if r.effect == OutboxEffect.EMAIL.value)

    # Pass 1: Google fails, so the email defers — not sent, and its attempt budget is untouched.
    first = await _drain(sqlite_session, sqlite_maker, execute, now=_BEFORE)
    assert email.id in first.deferred
    await sqlite_session.refresh(email)
    await sqlite_session.refresh(booking)
    assert email.attempts == 0
    assert sender.sent == []
    assert booking.meeting_url is None

    # Pass 2 (past the Google backoff + the defer delay): Google succeeds, then the email sends the
    # notice WITH the link — and the deferral never counted toward the dead-letter budget. A minute
    # of slack past the backoff. The drain's clock is now a REAL one (a lease is a wall-clock
    # deadline), so a retry stamped during the previous pass lands a few milliseconds after the
    # logical instant — sitting exactly ON the boundary would make this flaky for a reason that has
    # nothing to do with what it is testing.
    later = _BEFORE + backoff_delay(1) + timedelta(minutes=1)
    await _drain(sqlite_session, sqlite_maker, execute, now=later)
    await sqlite_session.refresh(email)
    await sqlite_session.refresh(booking)
    assert booking.meeting_url == "https://meet.example/evt-1"
    assert len(sender.sent) == 1
    assert booking.meeting_url in _email_body(sender.sent[0])
    assert email.attempts == 1


async def test_the_guest_link_we_email_carries_what_the_booking_page_reads(
    sqlite_session: AsyncSession,
    tenant_factory: Any,
) -> None:
    """RF-09: a guest with no account cancels or reschedules from the link in their email.

    This crosses a seam, and that is the whole point. The signed token authorises the action, but
    the public page renders from ``booking`` (and ``event_type``, to re-offer slots). Both halves
    were internally consistent and unit-tested while the link they formed always answered "missing
    context" — no assertion on either side of the seam could see it, so the guest could never
    self-serve. Assert the URL the guest actually receives.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )

    cancel_url, reschedule_url = await _mint_guest_links(
        sqlite_session, booking=booking, effects=_effects(), now=_BEFORE
    )

    # The parameter names below are the ones the booking page reads; they are the contract.
    cancel = parse_qs(urlsplit(cancel_url).query)
    assert cancel["booking"] == [str(booking.id)]
    assert cancel["token"], "the link must still carry the signed token that authorises the action"

    reschedule = parse_qs(urlsplit(reschedule_url).query)
    assert reschedule["booking"] == [str(booking.id)]
    # Rescheduling has to re-offer slots, so it needs the event type as well as the booking.
    assert reschedule["event_type"] == [str(event_type.id)]
    assert reschedule["token"]


# --------------------------------------------------------------------------------------
# max_per_day (RF-14) — the write-side gate.
# --------------------------------------------------------------------------------------

_TUE_SLOT_9 = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


async def test_create_booking_is_refused_once_the_day_is_at_its_cap(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A business that sets ``max_per_day=1`` must not take a second booking that day.

    The column was written, persisted and served by the API, and read by NOBODY — so the cap was a
    setting that did nothing at all. The refusal gets its OWN error (``DayFullError``): "the day is
    full" and "that time is taken" are different facts about the world, and a guest told the wrong
    one will keep trying other times on a day that has no room at any hour.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory, max_per_day=1)
    await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    with pytest.raises(DayFullError):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(event_type.id, _SLOT_11, guest_email="second@example.com"),
            now=_BEFORE,
        )

    assert await _active_count(sqlite_session, event_type) == 1


async def test_the_cap_is_per_day_and_does_not_leak_into_the_next_one(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A full Monday must not close Tuesday — the cap is a daily allowance, not a total."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory, max_per_day=1)
    await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    tuesday = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _TUE_SLOT_9, guest_email="tuesday@example.com"),
        now=_BEFORE,
    )

    assert tuesday.status == BookingStatus.CONFIRMED


async def test_a_cancellation_gives_back_the_days_last_place(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """Otherwise one guest could book and cancel and thereby shut the whole day, with an empty diary
    and no way for the business to reopen it."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory, max_per_day=1)
    first = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await cancel_booking(sqlite_session, tenant_id=tenant.id, booking_id=first.id, now=_BEFORE)

    replacement = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_11, guest_email="second@example.com"),
        now=_BEFORE,
    )

    assert replacement.status == BookingStatus.CONFIRMED


async def test_a_booking_can_be_rescheduled_within_the_day_it_alone_filled(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A reschedule MOVES an appointment; it does not add one.

    The predecessor is still ``confirmed`` while the new slot is validated, so counting it against
    the cap would tell the guest the day is full — because of the very booking they are moving. With
    ``max_per_day=1`` that would make a capped day impossible to reschedule within at all: the most
    ordinary request there is ("same day, an hour later") would be permanently refused.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory, max_per_day=1)
    original = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=original.id,
        new_start=_SLOT_11,
        now=_BEFORE,
    )

    assert moved.start_at == _SLOT_11
    assert moved.status == BookingStatus.CONFIRMED
    assert await _active_count(sqlite_session, event_type) == 1  # still one booking, moved


async def test_rescheduling_into_an_already_full_day_is_refused(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """The exclusion is narrow: it forgives the booking being MOVED, not the day it is moving INTO.

    Monday is full because of somebody ELSE's booking, so Tuesday's guest cannot slide into it — the
    predecessor's exemption must not become a way through the cap.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory, max_per_day=1)
    await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    tuesday = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _TUE_SLOT_9, guest_email="tuesday@example.com"),
        now=_BEFORE,
    )

    with pytest.raises(DayFullError):
        await reschedule_booking(
            sqlite_session,
            tenant_id=tenant.id,
            booking_id=tuesday.id,
            new_start=_SLOT_11,
            now=_BEFORE,
        )

    still = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=tuesday.id)
    assert still is not None
    assert still.status == BookingStatus.CONFIRMED  # a refused reschedule never touches the old row


async def test_no_cap_declared_leaves_the_booking_path_unchanged(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """``max_per_day = NULL`` is the default for every existing event type, and must stay a true
    absence of a cap — not a cap of zero that quietly stops the business taking bookings."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    assert event_type.max_per_day is None
    for hour, who in ((9, "a"), (10, "b"), (11, "c")):
        await create_booking(
            sqlite_session,
            tenant_id=tenant.id,
            params=_params(
                event_type.id,
                datetime(2026, 7, 6, hour, 0, tzinfo=UTC),
                guest_email=f"{who}@example.com",
            ),
            now=_BEFORE,
        )

    assert await _active_count(sqlite_session, event_type) == 3


# --------------------------------------------------------------------------------------
# active (RF-14) — "deleting" an event type must actually delete it, for guests.
# --------------------------------------------------------------------------------------


async def test_create_booking_on_a_deactivated_event_type_is_refused(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """``active = False`` is the product's DELETE. It must stop taking bookings.

    ``deactivate_event_type`` wrote the flag and nothing ever read it, so "delete this event type"
    removed it from nothing: the guest path went on accepting bookings for a service the business
    had withdrawn. The booking API is about to become public, which turns this from embarrassing
    into a hole.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    assert await deactivate_event_type(
        sqlite_session, tenant_id=tenant.id, event_type_id=event_type.id
    )

    with pytest.raises(EventTypeInactiveError):
        await create_booking(
            sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
        )

    assert await _active_count(sqlite_session, event_type) == 0


async def test_a_booking_on_a_deactivated_event_type_can_still_be_cancelled(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """==The one thing the fix must not break.==

    Deactivating an event type withdraws it from SALE. It cannot strand the guests who already hold
    an appointment: a booking nobody can cancel is worse than the bug being fixed — the guest is
    trapped, and the host keeps an hour they will never get back. So the cancel path deliberately
    keeps looking the event type up UNFILTERED (``_lock_and_reload_booking``), and only the paths
    that would take a NEW booking require ``active``.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await deactivate_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=event_type.id)

    cancelled = await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=booking.id, now=_BEFORE
    )

    assert cancelled.status == BookingStatus.CANCELLED


async def test_a_booking_on_a_deactivated_event_type_cannot_be_rescheduled(
    sqlite_session: AsyncSession,
    sqlite_maker: async_sessionmaker[AsyncSession],
    tenant_factory: Any,
) -> None:
    """A reschedule OPENS A NEW BOOKING row, so it is a sale — and the service is withdrawn.

    Refusing it explicitly (rather than letting it fall out of an empty slot list) is the honest
    answer: the business is no longer publishing this service on any day, so there is no time we
    could truthfully offer. The guest keeps the right to cancel; the business can reactivate.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session, tenant_id=tenant.id, params=_params(event_type.id, _SLOT_9), now=_BEFORE
    )
    await deactivate_event_type(sqlite_session, tenant_id=tenant.id, event_type_id=event_type.id)

    with pytest.raises(EventTypeInactiveError):
        await reschedule_booking(
            sqlite_session,
            tenant_id=tenant.id,
            booking_id=booking.id,
            new_start=_SLOT_11,
            now=_BEFORE,
        )

    still = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert still is not None
    assert still.status == BookingStatus.CONFIRMED
