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

The ``effects`` tests drive the bundle with in-memory fakes (a recording ``EmailSender`` and
``TaskRunner``) — still no network — to prove the durable side-effects are wired: the guest tokens
are minted in-txn, the email is ENQUEUED to the transactional outbox (not sent inline pre-commit),
the reminder is scheduled inline (best-effort), and draining the outbox runs the effect once (with
retry + idempotent re-drain). The persisted iCal SEQUENCE increments across the lifecycle (F1-08).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from cryptography.fernet import Fernet
from icalendar import Calendar
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.bookings import (
    AvailabilityUnavailableError,
    BookingEffects,
    BookingNotActiveError,
    BookingNotFoundError,
    BookingParams,
    EventTypeNotFoundError,
    SlotUnavailableError,
    cancel_booking,
    create_booking,
    get_booking,
    list_bookings,
    reschedule_booking,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import (
    OutboxEffect,
    backoff_delay,
    drain_outbox,
    email_dedupe_key,
    make_booking_effect_executor,
    run_google_effect,
)

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


async def _event_type(
    session: AsyncSession, tenant: Tenant, host: User, schedule: Schedule, *, slug: str = "intro"
) -> EventType:
    data = EventTypeCreate(
        host_id=host.id,
        schedule_id=schedule.id,
        slug=slug,
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 30,
    )
    return await create_event_type(session, tenant_id=tenant.id, data=data)


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, EventType]:
    tenant = await tenant_factory(session)
    host = await _first_user(session, tenant)
    schedule = await _schedule(session, tenant)
    event_type = await _event_type(session, tenant, host, schedule)
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
    return list((await session.scalars(stmt.order_by(Outbox.created_at))).all())


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


class _FailingRunner:
    """A :class:`TaskRunner` whose ``schedule`` always raises — drives the best-effort reminder
    contract (RF-10): a failing reminder scheduling must never roll the committed booking back."""

    def schedule(
        self,
        func: Any,
        *,
        run_at: datetime,
        job_id: str,
        kwargs: Any = None,
    ) -> None:
        raise RuntimeError("scheduler down")


class _RecordingRunner:
    """An in-memory :class:`TaskRunner` recording scheduled reminder jobs."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, datetime]] = []

    def schedule(
        self,
        func: Any,
        *,
        run_at: datetime,
        job_id: str,
        kwargs: Any = None,
    ) -> None:
        self.jobs.append((job_id, run_at))


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
    created event is only recorded on a call that actually succeeds.
    """

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
    """A BookingEffects with a host calendar connection, so the Google sync intents are enqueued."""
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
        connection=connection,
    )


# --------------------------------------------------------------------------------------
# create_booking
# --------------------------------------------------------------------------------------


async def test_create_on_offered_slot_confirms_and_queues_created_webhook(
    sqlite_session: AsyncSession, tenant_factory: Any
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


async def test_second_booking_on_the_same_slot_is_refused(
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    host = await _first_user(sqlite_session, tenant)
    # A connected but unreadable calendar with no covering cache → read_busy returns UNAVAILABLE,
    # so a booking is refused rather than risk a double-booking against an unknown calendar (RF-13).
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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


# --------------------------------------------------------------------------------------
# read paths + isolation
# --------------------------------------------------------------------------------------


async def test_get_and_list_bookings_filter_by_status(
    sqlite_session: AsyncSession, tenant_factory: Any
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
    sqlite_session: AsyncSession, tenant_factory: Any
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


def _effects(runner: Any = None) -> BookingEffects:
    # The email intent is enqueued unconditionally now (the drain owns the live sender), so the
    # bundle no longer carries a sender — tests hand a recording sender to the drain executor.
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
        reminder_runner=runner,
    )


async def test_create_mints_tokens_enqueues_the_email_intent_and_schedules_reminder(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    runner = _RecordingRunner()

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(runner),
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
    # The reminder is still scheduled inline (self-healing at fire time); Google unwired → no sync.
    assert runner.jobs == [(f"reminder:{booking.id}", _SLOT_9 - timedelta(hours=24))]
    assert booking.external_event_id is None
    assert booking.sequence == 0  # a fresh booking starts at the confirmation sequence


async def test_email_intent_is_enqueued_and_retryable_even_without_a_configured_sender(
    sqlite_session: AsyncSession, tenant_factory: Any
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
    execute = make_booking_effect_executor(sender=None, service_factory=None)
    report = await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)
    assert report.failed == [rows[0].id]
    assert rows[0].status == "failed"


async def test_draining_the_outbox_sends_the_confirmation_once_and_re_drain_is_idempotent(
    sqlite_session: AsyncSession, tenant_factory: Any
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

    execute = make_booking_effect_executor(sender=sender, service_factory=None)
    report = await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    assert report.delivered == [(await _outbox_rows(sqlite_session, booking_id=booking.id))[0].id]
    assert len(sender.sent) == 1
    seq, uid = _ics_seq_and_uid(sender.sent[0])
    assert seq == 0 and uid == booking.ical_uid

    # A re-drain executes nothing (the intent is delivered) and never mails the guest twice.
    again = await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)
    assert again.attempted == 0
    assert len(sender.sent) == 1


async def test_a_failing_send_marks_the_intent_for_retry_without_touching_the_booking(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )

    execute = make_booking_effect_executor(sender=_FailingSender(), service_factory=None)
    report = await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    # The send raised → the intent is parked failed for a backoff retry; the booking is untouched.
    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    assert report.failed == [rows[0].id]
    assert rows[0].status == "failed"
    assert rows[0].next_retry_at is not None
    persisted = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert persisted is not None and persisted.status == BookingStatus.CONFIRMED


async def test_cancel_enqueues_a_cancellation_email_intent(
    sqlite_session: AsyncSession, tenant_factory: Any
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


async def test_confirmation_and_cancellation_drained_together_carry_snapshotted_sequences(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """Even when the confirmation and cancellation emails drain in the SAME pass (both pending), the
    .ics carries the SEQUENCE snapshotted at ITS transition (0 then 1) over the one shared UID — the
    calendar converges to "cancelled" regardless of which email is sent first (the machine state is
    order-independent; the sequence, not delivery order, is what a client honors).
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

    # Drain BOTH email intents in one pass, then inspect by kind (subject), not by send order.
    execute = make_booking_effect_executor(sender=sender, service_factory=None)
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    by_seq = {seq: uid for seq, uid in (_ics_seq_and_uid(msg) for msg in sender.sent)}
    assert set(by_seq) == {0, 1}  # confirmation snapshotted 0, cancellation snapshotted 1
    assert set(by_seq.values()) == {booking.ical_uid}  # both address the same event


async def test_persisted_sequence_strictly_increases_across_reschedules_and_cancel(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """F1-08: confirmation starts at 0, successive reschedules strictly increase, and a cancellation
    uses the next value — proven on the actual emitted ``.ics`` SEQUENCE, drained after each step.
    """
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    execute = make_booking_effect_executor(sender=sender, service_factory=None)

    async def _drain() -> None:
        await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=_effects(),
    )
    await _drain()
    b2 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b1.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=_effects(),
    )
    await _drain()
    _SLOT_13 = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)
    b3 = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=b2.id,
        new_start=_SLOT_13,
        now=_BEFORE,
        effects=_effects(),
    )
    await _drain()
    await cancel_booking(
        sqlite_session, tenant_id=tenant.id, booking_id=b3.id, now=_BEFORE, effects=_effects()
    )
    await _drain()

    # The persisted counter advanced on every .ics-emitting mutation: the confirmation stays at 0,
    # each reschedule carries its predecessor + 1, and the cancellation bumps b3 one more (2 → 3).
    assert b1.sequence == 0  # a reschedule cancels the old row without bumping its sequence
    assert b2.sequence == 1
    assert b3.sequence == 3  # created at 2 by the reschedule, then bumped to 3 by the cancellation

    # The whole chain shares ONE stable UID (the reschedule successors inherit b1's), and the
    # emitted .ics sequences strictly increase over that single UID: confirmation 0, reschedules
    # 1 → 2, cancellation 3 — exactly what RFC 5545 requires for a client to honor every update.
    emitted = [_ics_seq_and_uid(msg) for msg in sender.sent]
    assert b1.ical_uid == b2.ical_uid == b3.ical_uid  # inherited across the reschedule chain
    assert {uid for _seq, uid in emitted} == {b1.ical_uid}  # every email addresses the same event
    sequences = [seq for seq, _uid in emitted]
    assert sequences == [0, 1, 2, 3]  # confirmation 0 < reschedule 1 < reschedule 2 < cancel 3


async def test_create_then_cancel_before_drain_leaves_no_orphaned_google_event(
    sqlite_session: AsyncSession, tenant_factory: Any
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

    execute = make_booking_effect_executor(sender=sender, service_factory=lambda _c: google)
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    # The create reconciled to "cancelled → no event" and skipped, so nothing was created and none
    # dangles; the booking never points at a live Google event.
    assert google.events_obj.created == []
    assert set(google.events_obj.created) == set(google.events_obj.deleted)
    refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert refreshed is not None and refreshed.external_event_id is None


async def test_create_then_reschedule_before_drain_yields_one_event_for_the_survivor(
    sqlite_session: AsyncSession, tenant_factory: Any
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

    execute = make_booking_effect_executor(sender=sender, service_factory=lambda _c: google)
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    # Exactly one live event, owned by the surviving booking b2; b1 (cancelled by the reschedule)
    # never keeps a live event.
    live = set(google.events_obj.created) - set(google.events_obj.deleted)
    assert len(live) == 1
    b2_refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=b2.id)
    assert b2_refreshed is not None and b2_refreshed.external_event_id in live
    b1_refreshed = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=b1.id)
    assert b1_refreshed is not None and b1_refreshed.external_event_id is None


async def test_cancelling_a_not_yet_synced_reschedule_deletes_the_original_event(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """Create + drain (event exists), reschedule WITHOUT draining, then cancel the successor and
    drain: the cancellation must delete the ORIGINAL event (resolved by walking the reschedule
    chain), leaving exactly one delete and no orphan — the successor never got an id of its own."""
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    google = _FakeGoogle()
    effects = await _google_effects(sqlite_session, tenant)
    execute = make_booking_effect_executor(sender=sender, service_factory=lambda _c: google)

    b1 = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)  # E1 now exists for b1
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
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    # Exactly one create (the original) and exactly one delete OF THAT SAME event — no orphan.
    assert google.events_obj.created == ["evt-1"]
    assert google.events_obj.deleted == ["evt-1"]


async def _google_row(session: AsyncSession, booking_id: uuid.UUID) -> Outbox:
    rows = await _outbox_rows(session, booking_id=booking_id)
    return next(r for r in rows if r.effect == OutboxEffect.GOOGLE.value)


async def test_reschedule_drained_before_the_original_upsert_never_recreates_the_old_event(
    sqlite_session: AsyncSession, tenant_factory: Any
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
    upsert = await _google_row(sqlite_session, b1.id)
    reschedule = await _google_row(sqlite_session, b2.id)

    def factory(_conn: Any) -> _FakeGoogle:
        return google

    # RESCHEDULE first: b2 is the chain's current booking → it creates the event.
    await run_google_effect(sqlite_session, reschedule, _BEFORE, service_factory=factory)
    # Then the original UPSERT: b1 was replaced → skipped, no second/old event ever created.
    await run_google_effect(sqlite_session, upsert, _BEFORE, service_factory=factory)

    assert google.events_obj.created == ["evt-1"]  # exactly one event, old one never recreated
    assert b2.external_event_id == "evt-1"
    assert b1.external_event_id is None


async def test_google_sync_runs_before_the_email_so_the_notice_carries_the_meet_link(
    sqlite_session: AsyncSession, tenant_factory: Any
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

    execute = make_booking_effect_executor(sender=sender, service_factory=lambda _c: google)
    await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)

    # Google ran first and set the Meet link; the confirmation email (drained after) carries it.
    assert booking.meeting_url == "https://meet.example/evt-1"
    assert len(sender.sent) == 1
    assert booking.meeting_url in _email_body(sender.sent[0])


async def test_email_defers_without_consuming_attempts_until_google_delivers_the_link(
    sqlite_session: AsyncSession, tenant_factory: Any
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
    execute = make_booking_effect_executor(sender=sender, service_factory=lambda _c: google)
    rows = await _outbox_rows(sqlite_session, booking_id=booking.id)
    email = next(r for r in rows if r.effect == OutboxEffect.EMAIL.value)

    # Pass 1: Google fails, so the email defers — not sent, and its attempt budget is untouched.
    first = await drain_outbox(sqlite_session, now=_BEFORE, execute=execute)
    assert email.id in first.deferred
    assert email.attempts == 0
    assert sender.sent == []
    assert booking.meeting_url is None

    # Pass 2 (past the Google backoff + the defer delay): Google succeeds, then the email sends the
    # notice WITH the link — and the deferral never counted toward the dead-letter budget.
    later = _BEFORE + backoff_delay(1)
    await drain_outbox(sqlite_session, now=later, execute=execute)
    assert booking.meeting_url == "https://meet.example/evt-1"
    assert len(sender.sent) == 1
    assert booking.meeting_url in _email_body(sender.sent[0])
    assert email.attempts == 1


async def test_a_failing_reminder_runner_never_rolls_back_the_booking(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    effects = BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
        reminder_runner=_FailingRunner(),
    )

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )

    # Reminder scheduling raised, but the booking stands (best-effort side-effect, RF-10).
    assert booking.status == BookingStatus.CONFIRMED
    persisted = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert persisted is not None
