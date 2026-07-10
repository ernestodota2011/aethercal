"""Offline service tests for the transactional outbox (F1-05).

Runs against the in-memory ``sqlite_session``. They prove the mechanism the booking service leans on
to make its post-commit effects durable and safe:

* an intent is enqueued as a ``pending`` row INSIDE the caller's transaction, idempotent per
  ``dedupe_key`` (a re-enqueue of the same transition is a no-op);
* a rolled-back transaction drops the intent (atomicity — the SQLite demonstration; the real PG
  proof lives in ``test_outbox_atomicity_pg.py``);
* draining runs a due intent through the injected ``execute`` exactly once and marks it delivered; a
  re-drain runs nothing (idempotent);
* a transient failure parks the intent for a backoff retry (not due before ``next_retry_at``), then
  a later drain delivers it, and a persistently failing intent is dead-lettered after
  ``max_attempts``;
* the live Google effect handler rebuilds the client from the connection and writes the created
  event id / meet url back onto the booking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Booking, Outbox, Schedule, Tenant, User
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import (
    DEFAULT_MAX_ATTEMPTS,
    GoogleOperation,
    OutboxEffect,
    backoff_delay,
    drain_outbox,
    enqueue_effect,
    google_dedupe_key,
    run_google_effect,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
_HALF_HOUR = timedelta(minutes=30)
_EMAIL_KEY = "email:confirmation"


# --------------------------------------------------------------------------------------
# Seeds + fakes.
# --------------------------------------------------------------------------------------


async def _seed_booking(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, Booking]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules={})
    session.add(schedule)
    await session.flush()
    event_type = await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        ),
    )
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_SLOT,
        end_at=_SLOT + _HALF_HOUR,
        status=BookingStatus.CONFIRMED,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return tenant, booking


async def _enqueue_email(session: AsyncSession, tenant: Tenant, booking: Booking) -> Outbox:
    row = await enqueue_effect(
        session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        effect=OutboxEffect.EMAIL,
        dedupe_key=_EMAIL_KEY,
        payload={"kind": "confirmation"},
    )
    assert row is not None
    return row


async def _all_rows(session: AsyncSession) -> list[Outbox]:
    return list((await session.scalars(select(Outbox))).all())


class _RecordingExecutor:
    """An ``execute`` that records the intents it ran; raises for the first ``fail_first`` calls."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.calls: list[uuid.UUID] = []
        self._fail_first = fail_first

    async def __call__(self, _session: AsyncSession, outbox: Outbox, _now: datetime) -> None:
        self.calls.append(outbox.id)
        if len(self.calls) <= self._fail_first:
            raise RuntimeError("transient effect failure")


class _FakeExecute:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _FakeEvents:
    def __init__(self, insert_result: Any) -> None:
        self._insert_result = insert_result
        self.deleted: list[str] = []

    def insert(
        self, *, calendarId: str, body: Any, conferenceDataVersion: int, sendUpdates: str
    ) -> _FakeExecute:
        return _FakeExecute(self._insert_result)

    def delete(self, *, calendarId: str, eventId: str, sendUpdates: str) -> _FakeExecute:
        self.deleted.append(eventId)
        return _FakeExecute(None)


class _FakeGoogleService:
    def __init__(self, *, insert_result: Any) -> None:
        self._events = _FakeEvents(insert_result)

    def events(self) -> _FakeEvents:
        return self._events


@pytest_asyncio.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


# --------------------------------------------------------------------------------------
# Enqueue.
# --------------------------------------------------------------------------------------


async def test_enqueue_persists_a_pending_intent_in_the_transaction(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)

    intent = await _enqueue_email(sqlite_session, tenant, booking)

    assert intent.status == "pending"
    assert intent.attempts == 0
    assert intent.effect == OutboxEffect.EMAIL.value
    assert intent.dedupe_key == _EMAIL_KEY
    assert intent.next_retry_at is None
    assert len(await _all_rows(sqlite_session)) == 1


async def test_enqueue_is_idempotent_on_the_dedupe_key(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)

    first = await _enqueue_email(sqlite_session, tenant, booking)
    second = await enqueue_effect(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        effect=OutboxEffect.EMAIL,
        dedupe_key=_EMAIL_KEY,
        payload={"kind": "confirmation"},
    )

    assert first is not None
    assert second is None  # the duplicate is a no-op, not a poisoning IntegrityError
    assert len(await _all_rows(sqlite_session)) == 1


async def test_a_rolled_back_transaction_drops_the_intent(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    await _enqueue_email(sqlite_session, tenant, booking)
    assert len(await _all_rows(sqlite_session)) == 1

    await sqlite_session.rollback()

    # The intent was enqueued in the transaction, so rolling it back drops it — it can never fire
    # for a mutation that never committed (the ordering bug the outbox closes).
    assert await _all_rows(sqlite_session) == []


# --------------------------------------------------------------------------------------
# Drain — once, retry, dead-letter.
# --------------------------------------------------------------------------------------


async def test_drain_runs_a_due_intent_once_and_a_re_drain_runs_nothing(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    intent = await _enqueue_email(sqlite_session, tenant, booking)
    executor = _RecordingExecutor()

    report = await drain_outbox(sqlite_session, now=NOW, execute=executor)

    assert executor.calls == [intent.id]
    assert report.delivered == [intent.id]
    assert intent.status == "delivered"
    assert intent.attempts == 1
    assert intent.next_retry_at is None

    again = await drain_outbox(sqlite_session, now=NOW, execute=executor)
    assert again.attempted == 0
    assert executor.calls == [intent.id]  # a delivered intent is never re-run


async def test_a_transient_failure_retries_after_backoff_then_delivers(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    intent = await _enqueue_email(sqlite_session, tenant, booking)
    executor = _RecordingExecutor(fail_first=1)

    first = await drain_outbox(sqlite_session, now=NOW, execute=executor)
    assert first.failed == [intent.id]
    assert intent.status == "failed"
    assert intent.attempts == 1
    retry_at = intent.next_retry_at
    assert retry_at is not None

    # Not due before next_retry_at: a drain at the failure instant runs nothing.
    early = await drain_outbox(sqlite_session, now=NOW, execute=executor)
    assert early.attempted == 0
    assert executor.calls == [intent.id]

    # Due at next_retry_at: the second attempt succeeds and the intent is delivered.
    due_now = NOW + backoff_delay(1)
    later = await drain_outbox(sqlite_session, now=due_now, execute=executor)
    assert later.delivered == [intent.id]
    assert intent.status == "delivered"
    assert intent.attempts == 2


async def test_drain_processes_at_most_batch_size_intents_per_pass(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    for i in range(3):
        await enqueue_effect(
            sqlite_session,
            tenant_id=tenant.id,
            booking_id=booking.id,
            effect=OutboxEffect.EMAIL,
            dedupe_key=f"email:k{i}",
            payload={"kind": "confirmation"},
        )
    executor = _RecordingExecutor()

    first = await drain_outbox(sqlite_session, now=NOW, execute=executor, batch_size=2)
    assert first.attempted == 2  # only two of the three due intents ran this pass

    second = await drain_outbox(sqlite_session, now=NOW, execute=executor, batch_size=2)
    assert second.attempted == 1  # the remainder drains on the next pass
    assert len(executor.calls) == 3


async def test_a_persistently_failing_intent_is_dead_lettered_after_max_attempts(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    intent = await _enqueue_email(sqlite_session, tenant, booking)
    executor = _RecordingExecutor(fail_first=DEFAULT_MAX_ATTEMPTS)

    now = NOW
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        await drain_outbox(sqlite_session, now=now, execute=executor)
        now = now + timedelta(hours=2)  # always past the capped backoff, so the intent is due again

    assert intent.status == "dead"
    assert intent.attempts == DEFAULT_MAX_ATTEMPTS
    assert intent.next_retry_at is None
    # A dead intent is terminal — a further drain never touches it again.
    assert (await drain_outbox(sqlite_session, now=now, execute=executor)).attempted == 0


# --------------------------------------------------------------------------------------
# The live Google effect handler.
# --------------------------------------------------------------------------------------


async def test_google_effect_creates_the_event_and_writes_it_back_to_the_booking(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant, booking = await _seed_booking(sqlite_session, tenant_factory)
    host = (await sqlite_session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    connection = await store_google_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json='{"token": "at"}'),
        fernet=fernet,
    )
    await sqlite_session.flush()

    intent = await enqueue_effect(
        sqlite_session,
        tenant_id=tenant.id,
        booking_id=booking.id,
        effect=OutboxEffect.GOOGLE,
        dedupe_key=google_dedupe_key(GoogleOperation.UPSERT),
        payload={
            "operation": GoogleOperation.UPSERT.value,
            "connection_id": str(connection.id),
            "external_event_id": None,
            "summary": "Intro",
            "start": booking.start_at.isoformat(),
            "end": booking.end_at.isoformat(),
            "timezone": "UTC",
            "guest_email": booking.guest_email,
        },
    )
    assert intent is not None
    service = _FakeGoogleService(
        insert_result={"id": "evt-42", "hangoutLink": "https://meet.google.com/abc-defg-hij"}
    )

    await run_google_effect(sqlite_session, intent, NOW, service_factory=lambda _conn: service)

    assert booking.external_event_id == "evt-42"
    assert booking.meeting_url == "https://meet.google.com/abc-defg-hij"
