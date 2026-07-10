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

A pair of tests drive the ``effects`` bundle with in-memory fakes (a recording ``EmailSender`` and
``TaskRunner``) — still no network — to prove the side-effects are wired and best-effort (an email
failure never rolls the booking back).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    EventType,
    GuestToken,
    Schedule,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
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


async def test_effects_mint_tokens_send_confirmation_and_schedule_reminder(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    sender = _RecordingSender()
    runner = _RecordingRunner()
    effects = BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
        sender=sender,
        reminder_runner=runner,
    )

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )

    # Two signed guest tokens (cancel + reschedule) are minted and stored (hashed) for this booking.
    tokens = list(
        (
            await sqlite_session.scalars(
                select(GuestToken).where(GuestToken.booking_id == booking.id)
            )
        ).all()
    )
    assert {t.purpose for t in tokens} == {"cancel", "reschedule"}
    # The confirmation email was sent once, and the reminder scheduled 24h before the start.
    assert len(sender.sent) == 1
    assert runner.jobs == [(f"reminder:{booking.id}", _SLOT_9 - timedelta(hours=24))]
    # No Google connection/service was supplied, so the calendar sync is skipped (left for retry).
    assert booking.external_event_id is None


async def test_a_failing_confirmation_email_never_rolls_back_the_booking(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, event_type = await _seed(sqlite_session, tenant_factory)
    effects = BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
        sender=_FailingSender(),
    )

    booking = await create_booking(
        sqlite_session,
        tenant_id=tenant.id,
        params=_params(event_type.id, _SLOT_9),
        now=_BEFORE,
        effects=effects,
    )

    # The email raised, but the booking stands (best-effort side-effect, RF-08).
    assert booking.status == BookingStatus.CONFIRMED
    persisted = await get_booking(sqlite_session, tenant_id=tenant.id, booking_id=booking.id)
    assert persisted is not None


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
