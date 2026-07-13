"""Offline service tests for the transactional outbox (F1-05 / R8).

Runs against an in-memory SQLite engine. They prove the mechanism the booking service leans on to
make its post-commit effects durable and safe:

* an intent is enqueued as a ``pending`` row INSIDE the caller's transaction, idempotent per
  ``dedupe_key`` (a re-enqueue of the same transition is a no-op);
* a rolled-back transaction drops the intent (the SQLite demonstration; the real PG proof lives in
  ``test_outbox_atomicity_pg.py``);
* draining claims a due intent, runs it through the injected ``execute`` exactly once and marks it
  delivered; a re-drain runs nothing;
* the R8 lifecycle: a row is ``claimed`` (with a lease) and COMMITTED before its effect runs, and
  the lease is released on settle; a worker that dies mid-send has its lease recovered WITHOUT being
  charged an attempt; a slow worker does not block another one;
* a transient failure parks the intent for a backoff retry, then a later drain delivers it; a
  persistently failing intent is dead-lettered after ``max_attempts``;
* the staleness contract: every effect declares itself, and a workflow step is classified BY TRIGGER
  (an ``on_cancel`` notice acts on a cancelled booking by definition, so it must be exempt from the
  guard or it would be marked delivered and never sent);
* the dispatch is exhaustive: an unimplemented effect raises loudly instead of silently becoming a
  Google Calendar call.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import Booking, Outbox, Schedule, Tenant, User
from aethercal.server.db.models.workflows import WorkflowTrigger
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import (
    DEFAULT_LEASE,
    DEFAULT_MAX_ATTEMPTS,
    PROVIDER_TIMEOUT_CEILING,
    GoogleOperation,
    OutboxEffect,
    OutboxReport,
    OutboxWork,
    Staleness,
    _Outcome,
    _settle,
    backoff_delay,
    claim_batch,
    drain_outbox,
    email_dedupe_key,
    enqueue_effect,
    google_dedupe_key,
    make_booking_effect_executor,
    recover_expired_leases,
    run_google_effect,
    staleness_policy,
    trigger_staleness,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
_HALF_HOUR = timedelta(minutes=30)
_EMAIL_KEY = "email:confirmation"
_LEASE = timedelta(minutes=5)

Sessionmaker = async_sessionmaker[AsyncSession]


# --------------------------------------------------------------------------------------
# Harness. The drain owns its transaction BOUNDARIES, so it needs a sessionmaker — one shared
# session could not express "commit the claim, then do the I/O with nothing open".
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[Sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_booking(
    maker: Sessionmaker, *, status: BookingStatus = BookingStatus.CONFIRMED
) -> tuple[uuid.UUID, uuid.UUID]:
    async with maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules={})
        session.add_all([host, schedule])
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
            status=status,
            guest_name="Ada Lovelace",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        return tenant.id, booking.id


async def _enqueue(  # noqa: PLR0913 - a test helper mirroring the enqueue contract
    maker: Sessionmaker,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    *,
    effect: OutboxEffect = OutboxEffect.EMAIL,
    dedupe_key: str = _EMAIL_KEY,
    payload: dict[str, Any] | None = None,
    next_retry_at: datetime | None = None,
) -> uuid.UUID:
    async with maker() as session, session.begin():
        row = await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            effect=effect,
            dedupe_key=dedupe_key,
            payload=payload if payload is not None else {"kind": "confirmation"},
            next_retry_at=next_retry_at,
        )
        # None = the row already existed (the conflict path). The re-enqueue tests look their row up
        # by booking, so they never need an id back from the swallowed insert.
        return row.id if row is not None else uuid.UUID(int=0)


async def _rows_for(maker: Sessionmaker, booking_id: uuid.UUID) -> list[Outbox]:
    async with maker() as session:
        return list(
            (await session.scalars(select(Outbox).where(Outbox.booking_id == booking_id))).all()
        )


async def _row(maker: Sessionmaker, intent_id: uuid.UUID) -> Outbox:
    async with maker() as session:
        row = await session.get(Outbox, intent_id)
        assert row is not None
        return row


class _RecordingExecutor:
    """An ``execute`` recording the intents it ran; raises for the first ``fail_first`` calls."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.calls: list[uuid.UUID] = []
        self._fail_first = fail_first

    async def __call__(self, work: OutboxWork, _now: datetime) -> None:
        self.calls.append(work.id)
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
        self.events_obj = _FakeEvents(insert_result)

    def events(self) -> _FakeEvents:
        return self.events_obj


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


# --------------------------------------------------------------------------------------
# Enqueue.
# --------------------------------------------------------------------------------------


async def test_enqueue_persists_a_pending_intent_in_the_transaction(maker: Sessionmaker) -> None:
    tenant_id, booking_id = await _seed_booking(maker)

    intent_id = await _enqueue(maker, tenant_id, booking_id)

    row = await _row(maker, intent_id)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.effect == OutboxEffect.EMAIL.value
    assert row.dedupe_key == _EMAIL_KEY
    assert row.next_retry_at is None
    assert row.claimed_by is None and row.lease_expires_at is None


async def test_enqueue_is_idempotent_on_the_dedupe_key(maker: Sessionmaker) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    await _enqueue(maker, tenant_id, booking_id)

    async with maker() as session, session.begin():
        second = await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            effect=OutboxEffect.EMAIL,
            dedupe_key=_EMAIL_KEY,
            payload={"kind": "confirmation"},
        )
    assert second is None  # the duplicate is a no-op, not a poisoning IntegrityError

    async with maker() as session:
        assert len((await session.scalars(select(Outbox))).all()) == 1


async def test_a_rolled_back_transaction_drops_the_intent(maker: Sessionmaker) -> None:
    tenant_id, booking_id = await _seed_booking(maker)

    async with maker() as session:
        await session.begin()
        # A real booking write FIRST — exactly what the booking service is doing when it enqueues.
        # It also matters mechanically: pysqlite only emits BEGIN when it sees DML, so without a
        # preceding write the enqueue's SAVEPOINT would run in autocommit and its RELEASE would
        # commit. That is a driver quirk, not an outbox behaviour, and the real caller never hits
        # it.
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        booking.guest_notes = "a booking mutation, in the same transaction as its intent"
        await session.flush()

        await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            effect=OutboxEffect.EMAIL,
            dedupe_key=_EMAIL_KEY,
            payload={"kind": "confirmation"},
        )
        await session.rollback()

    # The intent was enqueued in the transaction, so rolling it back drops it — it can never fire
    # for a mutation that never committed (the ordering bug the outbox closes).
    async with maker() as session:
        assert (await session.scalars(select(Outbox))).all() == []


async def test_a_future_next_retry_at_makes_the_outbox_a_scheduler(maker: Sessionmaker) -> None:
    """RF-10 rests on exactly this: a reminder is just an intent that is not DUE until ``start -
    24h``. Without it, the outbox could not have replaced the APScheduler jobstore."""
    tenant_id, booking_id = await _seed_booking(maker)
    async with maker() as session, session.begin():
        row = await enqueue_effect(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            effect=OutboxEffect.EMAIL,
            dedupe_key=email_dedupe_key(NotificationKind.REMINDER),
            payload={"kind": "reminder"},
            next_retry_at=NOW + timedelta(days=2),
        )
        assert row is not None

    executor = _RecordingExecutor()
    assert (await drain_outbox(maker, now=NOW, execute=executor)).attempted == 0
    assert executor.calls == []  # not due yet

    later = await drain_outbox(maker, now=NOW + timedelta(days=3), execute=executor)
    assert len(later.delivered) == 1


# --------------------------------------------------------------------------------------
# Drain — claim, deliver, retry, dead-letter.
# --------------------------------------------------------------------------------------


async def test_drain_runs_a_due_intent_once_and_a_re_drain_runs_nothing(
    maker: Sessionmaker,
) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)
    executor = _RecordingExecutor()

    report = await drain_outbox(maker, now=NOW, execute=executor)

    assert executor.calls == [intent_id]
    assert report.delivered == [intent_id]
    row = await _row(maker, intent_id)
    assert row.status == "delivered"
    assert row.attempts == 1
    assert row.next_retry_at is None
    # The lease is released on settle — a delivered row is not still "held" by a worker.
    assert row.claimed_by is None and row.lease_expires_at is None

    again = await drain_outbox(maker, now=NOW, execute=executor)
    assert again.attempted == 0
    assert executor.calls == [intent_id]  # a delivered intent is never re-run


async def test_the_row_is_claimed_and_committed_before_the_effect_runs(maker: Sessionmaker) -> None:
    """The R8 crux. The effect observes its OWN row already committed as ``claimed`` — only possible
    if the claim landed in its own transaction and released its row locks BEFORE the network I/O,
    instead of the whole batch staying locked open across every send."""
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)
    observed: dict[str, Any] = {}

    async def _execute(work: OutboxWork, _now: datetime) -> None:
        # Read the row from a SEPARATE session: an uncommitted claim would be invisible here.
        async with maker() as session:
            row = await session.get(Outbox, work.id)
            assert row is not None
            observed["status"] = row.status
            observed["claimed_by"] = row.claimed_by
            observed["lease_expires_at"] = row.lease_expires_at

    await drain_outbox(maker, now=NOW, execute=_execute, worker_id="worker-1", lease=_LEASE)

    assert observed["status"] == "claimed"
    assert observed["claimed_by"] == "worker-1"
    # SQLite drops tzinfo on the round-trip; normalise before comparing the instant.
    assert observed["lease_expires_at"].replace(tzinfo=UTC) == NOW + _LEASE
    assert (await _row(maker, intent_id)).status == "delivered"


async def test_a_transient_failure_retries_after_backoff_then_delivers(maker: Sessionmaker) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)
    executor = _RecordingExecutor(fail_first=1)

    first = await drain_outbox(maker, now=NOW, execute=executor)
    assert first.failed == [intent_id]
    row = await _row(maker, intent_id)
    assert row.status == "failed"
    assert row.attempts == 1
    assert row.next_retry_at is not None
    assert row.claimed_by is None  # the lease is released even on a failure

    # Not due before next_retry_at: a drain at the failure instant runs nothing.
    early = await drain_outbox(maker, now=NOW, execute=executor)
    assert early.attempted == 0
    assert executor.calls == [intent_id]

    # Due at next_retry_at: the second attempt succeeds and the intent is delivered.
    later = await drain_outbox(maker, now=NOW + backoff_delay(1), execute=executor)
    assert later.delivered == [intent_id]
    row = await _row(maker, intent_id)
    assert row.status == "delivered"
    assert row.attempts == 2


async def test_drain_processes_at_most_batch_size_intents_per_pass(maker: Sessionmaker) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    for i in range(3):
        await _enqueue(maker, tenant_id, booking_id, dedupe_key=f"email:k{i}")
    executor = _RecordingExecutor()

    first = await drain_outbox(maker, now=NOW, execute=executor, batch_size=2)
    assert first.attempted == 2  # only two of the three due intents ran this pass

    second = await drain_outbox(maker, now=NOW, execute=executor, batch_size=2)
    assert second.attempted == 1  # the remainder drains on the next pass
    assert len(executor.calls) == 3


async def test_a_persistently_failing_intent_is_dead_lettered_after_max_attempts(
    maker: Sessionmaker,
) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)
    executor = _RecordingExecutor(fail_first=DEFAULT_MAX_ATTEMPTS)

    now = NOW
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        await drain_outbox(maker, now=now, execute=executor)
        now = now + timedelta(hours=2)  # always past the capped backoff, so the intent is due again

    row = await _row(maker, intent_id)
    assert row.status == "dead"
    assert row.attempts == DEFAULT_MAX_ATTEMPTS
    assert row.next_retry_at is None
    # A dead intent is terminal — a further drain never touches it again.
    assert (await drain_outbox(maker, now=now, execute=executor)).attempted == 0


# --------------------------------------------------------------------------------------
# The lease: a worker that dies mid-send must not strand its rows, nor be charged for dying.
# --------------------------------------------------------------------------------------


async def test_an_expired_lease_is_recovered_without_consuming_an_attempt(
    maker: Sessionmaker,
) -> None:
    """A crashed worker's row comes back — and its ``attempts`` is untouched. Charging it would push
    a perfectly healthy intent toward the dead-letter for somebody else's crash."""
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)

    async def _die(_work: OutboxWork, _now: datetime) -> None:
        raise KeyboardInterrupt  # the process vanishes mid-send; the row is never settled

    with pytest.raises(KeyboardInterrupt):
        await drain_outbox(maker, now=NOW, execute=_die, worker_id="doomed", lease=_LEASE)

    stranded = await _row(maker, intent_id)
    assert stranded.status == "claimed"
    assert stranded.claimed_by == "doomed"
    assert stranded.attempts == 0

    # While the lease is still valid, nobody may steal the row.
    executor = _RecordingExecutor()
    early = await drain_outbox(maker, now=NOW + timedelta(minutes=1), execute=executor)
    assert early.attempted == 0
    assert executor.calls == []

    # Once it expires, the next pass recovers it and a healthy worker delivers it.
    report = await drain_outbox(maker, now=NOW + timedelta(minutes=6), execute=executor)
    assert report.recovered == [intent_id]
    assert report.delivered == [intent_id]
    row = await _row(maker, intent_id)
    assert row.status == "delivered"
    assert row.attempts == 1  # ONE attempt: the crash was never charged to the effect


async def test_a_slow_worker_does_not_block_another_worker(maker: Sessionmaker) -> None:
    """Worker A holds a claim on intent 1 across a long send; worker B must still drain intent 2.

    Under the old design the whole batch stayed ``FOR UPDATE``-locked for the length of the tick, so
    a slow send stalled everything behind it. With the claim committed up front, B's claim simply
    skips A's row and takes its own."""
    tenant_id, booking_id = await _seed_booking(maker)
    first = await _enqueue(maker, tenant_id, booking_id, dedupe_key="email:a")
    second = await _enqueue(maker, tenant_id, booking_id, dedupe_key="email:b")
    drained_by_b: list[uuid.UUID] = []

    async def _slow_a(_work: OutboxWork, _now: datetime) -> None:
        # A is mid-"network call" for its intent. While it hangs here, B runs a whole pass.
        b = _RecordingExecutor()
        report = await drain_outbox(maker, now=NOW, execute=b, worker_id="B", batch_size=1)
        drained_by_b.extend(report.delivered)

    await drain_outbox(maker, now=NOW, execute=_slow_a, worker_id="A", batch_size=1)

    # B got a DIFFERENT intent (never A's claimed one) and carried it all the way to delivered while
    # A was still "sending".
    assert drained_by_b == [second]
    assert second != first
    assert (await _row(maker, first)).status == "delivered"
    assert (await _row(maker, second)).status == "delivered"


# --------------------------------------------------------------------------------------
# The staleness contract. This is the money/slot bug: `_is_chain_current` is False for a CANCELLED
# booking BY CONSTRUCTION, and a refund / hold-expiry acts on a cancelled booking BY DEFINITION.
# Gate them on it and the refund never refunds and the hold never expires.
# --------------------------------------------------------------------------------------


def test_every_effect_declares_a_staleness_policy() -> None:
    """The table is exhaustive. An effect that forgets to declare itself must FAIL loudly, never
    inherit a default — a terminal message that inherits SUBJECT is a message marked delivered and
    never sent."""
    payloads: dict[OutboxEffect, dict[str, Any]] = {
        OutboxEffect.EMAIL: {"kind": "confirmation"},
        OutboxEffect.GOOGLE: {"operation": "upsert"},
        OutboxEffect.NOTIFY: {"trigger": WorkflowTrigger.BEFORE_START.value},
    }
    for effect in OutboxEffect:
        assert staleness_policy(effect, payloads[effect]) in set(Staleness)


@pytest.mark.parametrize(
    "trigger",
    [WorkflowTrigger.ON_CANCEL, WorkflowTrigger.ON_NO_SHOW, WorkflowTrigger.AFTER_END],
)
def test_a_terminal_trigger_is_exempt_from_the_staleness_guard(trigger: WorkflowTrigger) -> None:
    """The bug this kills: an ``on_cancel`` step acts on a booking that is CANCELLED, and
    ``_is_chain_current`` is False for a cancelled booking BY CONSTRUCTION. Gate it on staleness and
    the cancellation notice is marked delivered and NEVER SENT — the guest is never told."""
    assert trigger_staleness(trigger) is Staleness.EXEMPT
    assert staleness_policy(OutboxEffect.NOTIFY, {"trigger": trigger.value}) is Staleness.EXEMPT


@pytest.mark.parametrize("trigger", [WorkflowTrigger.ON_BOOKING, WorkflowTrigger.BEFORE_START])
def test_a_forward_looking_trigger_is_subject_to_the_staleness_guard(
    trigger: WorkflowTrigger,
) -> None:
    """These speak about an appointment that is still supposed to happen. If the chain moved on, the
    message is simply wrong (a reminder for a slot that was rescheduled away)."""
    assert trigger_staleness(trigger) is Staleness.SUBJECT
    assert staleness_policy(OutboxEffect.NOTIFY, {"trigger": trigger.value}) is Staleness.SUBJECT


def test_every_workflow_trigger_is_classified() -> None:
    """Exhaustive: a trigger added without classifying it must not inherit a silent default — that
    is either a dropped cancellation notice or a reminder sent for a dead booking."""
    for trigger in WorkflowTrigger:
        assert trigger_staleness(trigger) in set(Staleness)


def test_the_terminal_email_and_calendar_effects_are_exempt() -> None:
    assert staleness_policy(OutboxEffect.EMAIL, {"kind": "cancellation"}) is Staleness.EXEMPT
    assert staleness_policy(OutboxEffect.GOOGLE, {"operation": "delete"}) is Staleness.EXEMPT


def test_the_informational_effects_are_subject_to_the_staleness_guard() -> None:
    assert staleness_policy(OutboxEffect.EMAIL, {"kind": "confirmation"}) is Staleness.SUBJECT
    assert staleness_policy(OutboxEffect.EMAIL, {"kind": "reschedule"}) is Staleness.SUBJECT
    assert staleness_policy(OutboxEffect.EMAIL, {"kind": "reminder"}) is Staleness.SUBJECT
    assert staleness_policy(OutboxEffect.GOOGLE, {"operation": "upsert"}) is Staleness.SUBJECT


# --------------------------------------------------------------------------------------
# Exhaustive dispatch: no effect silently becomes a Google call.
# --------------------------------------------------------------------------------------


async def test_an_unimplemented_effect_raises_instead_of_becoming_a_google_call(
    maker: Sessionmaker,
) -> None:
    """The old dispatcher was ``if EMAIL … else GOOGLE`` — the ``else`` ASSUMED Google, so every new
    effect would have been executed as a Google Calendar call. Now it raises explicitly."""
    google = _FakeGoogleService(insert_result={"id": "evt-1", "hangoutLink": "https://meet/x"})
    execute = make_booking_effect_executor(
        sessionmaker=maker, sender=None, service_factory=lambda _c: google
    )
    work = OutboxWork(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        booking_id=uuid.uuid4(),
        effect=OutboxEffect.NOTIFY,
        dedupe_key="wf:1:2:email",
        payload={},
        attempts=0,
        claimed_by="test-worker",
    )

    with pytest.raises(NotImplementedError, match="notify"):
        await execute(work, NOW)

    assert google.events_obj.deleted == []  # nothing leaked into the calendar client


# --------------------------------------------------------------------------------------
# The live Google effect handler.
# --------------------------------------------------------------------------------------


async def test_google_effect_creates_the_event_and_writes_it_back_to_the_booking(
    maker: Sessionmaker, fernet: Fernet
) -> None:
    tenant_id, booking_id = await _seed_booking(maker)
    async with maker() as session, session.begin():
        host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).one()
        connection = await store_google_connection(
            session,
            tenant_id=tenant_id,
            user_id=host.id,
            credential=GoogleCredential(
                account_email="host@gmail.com", token_json='{"token": "at"}'
            ),
            fernet=fernet,
        )
        connection_id = connection.id

    payload: dict[str, Any] = {
        "operation": GoogleOperation.UPSERT.value,
        "connection_id": str(connection_id),
        "external_event_id": None,
        "summary": "Intro",
        "start": _SLOT.isoformat(),
        "end": (_SLOT + _HALF_HOUR).isoformat(),
        "timezone": "UTC",
        "guest_email": "ada@example.com",
    }
    intent_id = await _enqueue(
        maker,
        tenant_id,
        booking_id,
        effect=OutboxEffect.GOOGLE,
        dedupe_key=google_dedupe_key(GoogleOperation.UPSERT),
        payload=payload,
    )
    service = _FakeGoogleService(
        insert_result={"id": "evt-42", "hangoutLink": "https://meet.google.com/abc-defg-hij"}
    )
    work = OutboxWork(
        id=intent_id,
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.GOOGLE,
        dedupe_key=google_dedupe_key(GoogleOperation.UPSERT),
        payload=payload,
        attempts=0,
        claimed_by="test-worker",
    )

    await run_google_effect(maker, work, NOW, service_factory=lambda _conn: service)

    async with maker() as session:
        booking = await session.get(Booking, booking_id)
    assert booking is not None
    assert booking.external_event_id == "evt-42"
    assert booking.meeting_url == "https://meet.google.com/abc-defg-hij"


# --------------------------------------------------------------------------------------
# The lease is only half a mechanism without an OWNERSHIP check at settle time.
# --------------------------------------------------------------------------------------


async def test_a_worker_that_lost_its_lease_DISCARDS_its_result_instead_of_stomping(
    maker: Sessionmaker, caplog: pytest.LogCaptureFixture
) -> None:
    """The race the lease alone does not close.

    Worker A claims a row. Its network I/O overruns the TTL. The recovery pass hands the row back to
    ``pending`` and worker B claims it. Now A finishes and settles — and without an ownership check
    it writes its stale result ON TOP of B's live claim, marking ``delivered`` an intent B is still
    executing.

    So the settle is a CONDITIONAL update. A no longer matches ``claimed_by``, so its result is
    DISCARDED — and said out loud, because writing where you have lost the right is the same silent
    no-op as before, just pointed the other way."""
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)

    # Worker A claims the row (and then "hangs" in its send for longer than the TTL).
    async with maker() as session, session.begin():
        claimed = await claim_batch(session, now=NOW, worker_id="A", lease=_LEASE)
    work_a = claimed[0]
    assert work_a.claimed_by == "A"

    # The lease elapses; the recovery pass returns the row, and worker B claims it.
    later = NOW + _LEASE + timedelta(seconds=1)
    async with maker() as session, session.begin():
        assert await recover_expired_leases(session, now=later) == [intent_id]
    async with maker() as session, session.begin():
        reclaimed = await claim_batch(session, now=later, worker_id="B", lease=_LEASE)
    assert [w.id for w in reclaimed] == [intent_id]

    # NOW worker A comes back from its send and tries to settle. It must not be able to.
    report = OutboxReport()
    with caplog.at_level("ERROR"):
        await _settle(maker, work_a, now=later, outcome=_Outcome.DELIVERED, report=report)

    assert report.delivered == [], "the stale worker marked the intent delivered"
    assert report.lost == [intent_id]
    assert any("LEASE LOST" in record.getMessage() for record in caplog.records)

    # B's claim is untouched: still claimed, still B's, still zero attempts.
    row = await _row(maker, intent_id)
    assert row.status == "claimed"
    assert row.claimed_by == "B"
    assert row.attempts == 0


async def test_the_lease_holder_settles_normally(maker: Sessionmaker) -> None:
    """The other side of the same coin: the worker that still holds the lease writes as usual."""
    tenant_id, booking_id = await _seed_booking(maker)
    intent_id = await _enqueue(maker, tenant_id, booking_id)
    async with maker() as session, session.begin():
        claimed = await claim_batch(session, now=NOW, worker_id="A", lease=_LEASE)

    report = OutboxReport()
    await _settle(maker, claimed[0], now=NOW, outcome=_Outcome.DELIVERED, report=report)

    assert report.delivered == [intent_id]
    assert report.lost == []
    row = await _row(maker, intent_id)
    assert row.status == "delivered"
    assert row.claimed_by is None  # the lease is released


def test_the_provider_timeout_ceiling_is_strictly_under_the_lease() -> None:
    """The invariant that keeps the race above from ever happening in the first place. The lease
    does not renew itself, so a send that outlives the TTL loses its claim AFTER the provider
    already did the work — and the guest can be messaged twice. Bounding every provider call below
    the TTL is what makes that unreachable rather than merely unlikely."""
    assert PROVIDER_TIMEOUT_CEILING < DEFAULT_LEASE
