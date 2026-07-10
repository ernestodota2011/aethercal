"""The outbox-atomicity proof (PostgreSQL only, db-marked).

The offline suite shows the enqueue-in-txn + rollback semantics on SQLite; only a real server proves
that a booking mutation and its transactional-outbox intent commit together — or roll back together.
This is the crux of the F1-05 fix: an external effect (email/Google) can never fire for a booking
whose transaction later rolled back, because the intent is not a separate best-effort side-effect
but a row written in the SAME transaction as the booking.

It reuses the ``app`` fixture (real FastAPI over PostgreSQL, schema wiped per test).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Booking, EventType, Outbox, Schedule, Tenant, User
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.scheduler import run_outbox_drain_once
from aethercal.server.services.bookings import BookingEffects, BookingParams, create_booking
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import (
    GoogleOperation,
    OutboxEffect,
    email_dedupe_key,
    enqueue_effect,
    google_dedupe_key,
    make_booking_effect_executor,
)
from aethercal.server.services.slots import compute_slots

pytestmark = pytest.mark.db

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class _FakeExecute:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _FakeEvents:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._n = 0

    def insert(
        self, *, calendarId: str, body: Any, conferenceDataVersion: int, sendUpdates: str
    ) -> _FakeExecute:
        self._n += 1
        event_id = f"evt-{self._n}"
        self.created.append(event_id)
        return _FakeExecute({"id": event_id, "hangoutLink": f"https://meet.example/{event_id}"})

    def delete(self, *, calendarId: str, eventId: str, sendUpdates: str) -> _FakeExecute:
        self.deleted.append(eventId)
        return _FakeExecute(None)


class _FakeGoogle:
    def __init__(self) -> None:
        self.events_obj = _FakeEvents()

    def events(self) -> _FakeEvents:
        return self.events_obj


def _effects() -> BookingEffects:
    # The email intent is enqueued unconditionally now (the drain owns the live sender), so the
    # bundle needs only the signer + base URL to mint the guest links in-transaction.
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"),
        booking_base_url="https://book.example.com",
    )


def _params(event_type_id: uuid.UUID, start: datetime) -> BookingParams:
    return BookingParams(
        event_type_id=event_type_id,
        start=start,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )


async def _seed(app: FastAPI) -> tuple[uuid.UUID, uuid.UUID, datetime, datetime]:
    """Commit a tenant + host + open schedule + event type; return ids, an offered slot, and now."""
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Outbox Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_ALWAYS_OPEN)
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

        now = datetime.now(UTC)
        tomorrow = (now + timedelta(days=1)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=tomorrow,
            window_to=tomorrow,
            now=now,
        )
        assert result is not None and result.slots
        return tenant.id, event_type.id, result.slots[0].start, now


async def _outbox_count(
    sessionmaker: async_sessionmaker[AsyncSession], booking_id: uuid.UUID
) -> int:
    async with sessionmaker() as session:
        return (
            await session.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.booking_id == booking_id)
            )
        ) or 0


async def test_a_rolled_back_booking_leaves_no_outbox_intent(app: FastAPI) -> None:
    tenant_id, event_type_id, start, now = await _seed(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    # Create the booking + enqueue its email intent in one transaction, then ROLL BACK.
    async with sessionmaker() as session:
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=_params(event_type_id, start),
            now=now,
            effects=_effects(),
        )
        booking_id = booking.id
        # Both the booking and its intent are visible inside the uncommitted transaction.
        pending = await session.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.booking_id == booking_id)
        )
        assert pending == 1
        await session.rollback()

    # After the rollback neither the booking nor its outbox intent persisted — the effect can never
    # fire for a booking that never committed.
    async with sessionmaker() as session:
        assert await session.get(Booking, booking_id) is None
    assert await _outbox_count(sessionmaker, booking_id) == 0


async def test_a_committed_booking_persists_its_outbox_intent(app: FastAPI) -> None:
    tenant_id, event_type_id, start, now = await _seed(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    async with sessionmaker() as session, session.begin():
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=_params(event_type_id, start),
            now=now,
            effects=_effects(),
        )
        booking_id = booking.id

    # The intent committed atomically with the booking and is now waiting for the drainer.
    async with sessionmaker() as session:
        rows = list(
            (await session.scalars(select(Outbox).where(Outbox.booking_id == booking_id))).all()
        )
    assert len(rows) == 1
    assert rows[0].effect == OutboxEffect.EMAIL.value
    assert rows[0].dedupe_key == email_dedupe_key(NotificationKind.CONFIRMATION)
    assert rows[0].status == "pending"


async def test_concurrent_drains_never_double_execute_a_bookings_google_sync(app: FastAPI) -> None:
    """Two workers draining the same booking's Google sync concurrently must run it exactly ONCE:
    ``FOR UPDATE SKIP LOCKED`` hands the single row to one worker only, and the per-``ical_uid``
    advisory lock + reconciliation run on real PostgreSQL without deadlock or a double event."""
    tenant_id, _event_type_id, start, now = await _seed(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    # A confirmed booking with a linked calendar + a single queued Google upsert (built directly so
    # the test targets the concurrent-drain path, not slot validation).
    async with sessionmaker() as session, session.begin():
        host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).one()
        event_type = (
            await session.scalars(select(EventType).where(EventType.tenant_id == tenant_id))
        ).one()
        connection = await store_google_connection(
            session,
            tenant_id=tenant_id,
            user_id=host.id,
            credential=GoogleCredential(account_email="h@gmail.com", token_json='{"token": "t"}'),
            fernet=Fernet(derive_fernet_key("test-app-secret")),
        )
        await session.flush()
        booking = Booking(
            tenant_id=tenant_id,
            event_type_id=event_type.id,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=BookingStatus.CONFIRMED,
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
            ical_uid="chain-1@aethercal",
        )
        session.add(booking)
        await session.flush()
        await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking.id,
            effect=OutboxEffect.GOOGLE,
            dedupe_key=google_dedupe_key(GoogleOperation.UPSERT),
            payload={
                "operation": GoogleOperation.UPSERT.value,
                "connection_id": str(connection.id),
                "external_event_id": None,
                "summary": "Intro",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "timezone": "UTC",
                "guest_email": "ada@example.com",
            },
        )
        booking_id = booking.id

    google = _FakeGoogle()
    execute = make_booking_effect_executor(
        sender=_RecordingSender(), service_factory=lambda _c: google
    )
    await asyncio.gather(
        run_outbox_drain_once(sessionmaker=sessionmaker, execute=execute, now=now),
        run_outbox_drain_once(sessionmaker=sessionmaker, execute=execute, now=now),
    )

    # Exactly one event created (never two), and the booking points at it.
    assert len(google.events_obj.created) == 1
    async with sessionmaker() as session:
        final = await session.get(Booking, booking_id)
    assert final is not None and final.external_event_id == google.events_obj.created[0]


def _google_sync_payload(
    connection_id: uuid.UUID, operation: GoogleOperation, start: datetime
) -> dict[str, Any]:
    return {
        "operation": operation.value,
        "connection_id": str(connection_id),
        "external_event_id": None,
        "summary": "Intro",
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=30)).isoformat(),
        "timezone": "UTC",
        "guest_email": "ada@example.com",
    }


async def test_concurrent_reschedule_before_upsert_never_recreates_the_replaced_event(
    app: FastAPI,
) -> None:
    """Two workers, inverted order: a chain's RESCHEDULE (successor) and the original's UPSERT drain
    concurrently. The replaced predecessor's UPSERT must be SKIPPED — exactly one event exists, for
    the chain's current booking, and the old one is never recreated (per-``ical_uid`` advisory lock
    + chain-current reconciliation)."""
    tenant_id, _event_type_id, start, now = await _seed(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    async with sessionmaker() as session, session.begin():
        host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).one()
        event_type = (
            await session.scalars(select(EventType).where(EventType.tenant_id == tenant_id))
        ).one()
        connection = await store_google_connection(
            session,
            tenant_id=tenant_id,
            user_id=host.id,
            credential=GoogleCredential(account_email="h@gmail.com", token_json='{"token": "t"}'),
            fernet=Fernet(derive_fernet_key("test-app-secret")),
        )
        await session.flush()
        common = dict(
            tenant_id=tenant_id,
            event_type_id=event_type.id,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
            ical_uid="chain-2@aethercal",  # the successor inherits the predecessor's UID
        )
        b1 = Booking(status=BookingStatus.CANCELLED, **common)  # replaced predecessor
        session.add(b1)
        await session.flush()
        b2 = Booking(status=BookingStatus.CONFIRMED, rescheduled_from_id=b1.id, **common)  # current
        session.add(b2)
        await session.flush()
        # The original's UPSERT (for b1) and the successor's RESCHEDULE (for b2), neither drained.
        await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=b1.id,
            effect=OutboxEffect.GOOGLE,
            dedupe_key=google_dedupe_key(GoogleOperation.UPSERT),
            payload=_google_sync_payload(connection.id, GoogleOperation.UPSERT, start),
        )
        await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=b2.id,
            effect=OutboxEffect.GOOGLE,
            dedupe_key=google_dedupe_key(GoogleOperation.RESCHEDULE),
            payload=_google_sync_payload(connection.id, GoogleOperation.RESCHEDULE, start),
        )
        b1_id, b2_id = b1.id, b2.id

    google = _FakeGoogle()
    execute = make_booking_effect_executor(
        sender=_RecordingSender(), service_factory=lambda _c: google
    )
    await asyncio.gather(
        run_outbox_drain_once(sessionmaker=sessionmaker, execute=execute, now=now),
        run_outbox_drain_once(sessionmaker=sessionmaker, execute=execute, now=now),
    )

    # Exactly one event — for the current booking b2 — and the replaced one is never recreated.
    assert len(google.events_obj.created) == 1
    async with sessionmaker() as session:
        b1_final = await session.get(Booking, b1_id)
        b2_final = await session.get(Booking, b2_id)
    assert b1_final is not None and b1_final.external_event_id is None
    assert b2_final is not None and b2_final.external_event_id == google.events_obj.created[0]
