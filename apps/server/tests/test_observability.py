"""Offline tests for R9 — the observability that makes a dead scheduler VISIBLE.

The hole this closes, stated plainly: **today, if the drain process dies, the emails stop going out
in silence and nothing reports it.** So these tests are about the EFFECTIVE state, never the
apparent one:

* the backlog gauges count what is really in the table, keyed off the row's own status vocabulary
  (``OutboxStatus``) — a gauge for a status nobody writes any more would sit at a reassuring ``0``
  forever;
* the alertable signal is the **DUE** backlog and the **age of the oldest due** intent, not the raw
  ``pending`` count. A reminder queued for a booking three weeks out is ``pending`` and perfectly
  healthy; counting it as backlog buries the one number that really does grow without bound when
  the drain stops;
* ``lost`` (a send that outran its lease → the guest may have been messaged twice) is kept OUT of
  the noise of ``voided_midflight`` (a routine cancellation retiring a step mid-flight). An alarm
  that also fires on every ordinary cancellation is an alarm nobody reads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    OutboxStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.db.pools import BypassReason, WorkerPools, mark_bypass
from aethercal.server.observability import (
    DRAIN_COUNTERS,
    DrainCounters,
    MetricsSnapshot,
    collect_metrics,
    observe_drain,
    render_human,
    render_prometheus,
)
from aethercal.server.services.outbox import OutboxEffect, OutboxReport, OutboxWork, drain_outbox


@pytest.fixture(autouse=True)
def _the_offline_session_is_a_scan_session(sqlite_session: AsyncSession) -> None:
    """==Mark the fixture, because ``collect_metrics`` now REFUSES a session with no bypass.==

    It reads across EVERY business by design, and it fills its gauges with zeros wherever it finds
    nothing. Under row-level security, on the app role, those two facts together produce
    ``outbox.due = 0`` and ``status: ready`` - for ever, with the queue on fire. So it fails rather
    than degrading.

    The detection is a MARKER on ``session.info`` rather than ``SELECT current_user``, and this
    fixture is exactly why: these eleven tests run on SQLite, which has no roles at all. Detecting
    by
    role would have forced the whole suite into the Postgres job and cost real offline coverage. The
    marker is set by ``scan_session`` in the product - a structural test asserts nothing else does -
    and by a test fixture here, where there is no RLS to bypass in the first place.
    """
    mark_bypass(sqlite_session, BypassReason.OPERATOR_METRICS)


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


_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


async def _tenant_with_event_type(session: AsyncSession) -> tuple[Tenant, EventType]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
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


async def _booking(
    session: AsyncSession,
    tenant: Tenant,
    event_type: EventType,
    *,
    status: BookingStatus = BookingStatus.CONFIRMED,
    start: datetime = _NOW,
) -> Booking:
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=status,
        # Confirmed/cancelled ⇒ stamped; only an unpaid hold has no confirmation (B-05a).
        confirmed_at=None if status is BookingStatus.PENDING else start - timedelta(days=1),
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return booking


async def _intent(  # noqa: PLR0913 - a test factory: one keyword per column under test
    session: AsyncSession,
    booking: Booking,
    *,
    status: OutboxStatus,
    created_at: datetime,
    next_retry_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
) -> Outbox:
    row = Outbox(
        tenant_id=booking.tenant_id,
        booking_id=booking.id,
        effect=OutboxEffect.EMAIL.value,
        dedupe_key=f"k-{uuid.uuid4().hex[:8]}",
        payload={},
        status=status.value,
        attempts=0,
        created_at=created_at,
        next_retry_at=next_retry_at,
        lease_expires_at=lease_expires_at,
    )
    session.add(row)
    await session.flush()
    return row


# --------------------------------------------------------------------------------------
# The backlog: counted by the row's OWN status vocabulary.
# --------------------------------------------------------------------------------------


async def test_the_backlog_reports_a_gauge_for_every_status_the_drain_can_write(
    sqlite_session: AsyncSession,
) -> None:
    """Every member of ``OutboxStatus``, present or not. A status that only appears in the output
    once it has rows is a gauge that reads "absent" and "zero" identically — and a dashboard cannot
    alert on a series that does not exist."""
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type)
    await _intent(sqlite_session, booking, status=OutboxStatus.DEAD, created_at=_NOW)

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert set(snapshot.outbox_by_status) == {status.value for status in OutboxStatus}
    assert snapshot.outbox_by_status["dead"] == 1
    assert snapshot.outbox_by_status["pending"] == 0


async def test_a_future_scheduled_intent_is_pending_but_is_not_due_backlog(
    sqlite_session: AsyncSession,
) -> None:
    """==The distinction the whole alarm hangs on.== A 24 h reminder for a booking three weeks out
    is ``pending`` and entirely healthy. Count it as backlog and the number that really does grow
    without bound when the drain dies is buried inside it."""
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type)
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.PENDING,
        created_at=_NOW,
        next_retry_at=_NOW + timedelta(days=21),
    )

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.outbox_by_status["pending"] == 1
    assert snapshot.outbox_due == 0
    assert snapshot.outbox_oldest_due_age_seconds == 0.0


async def test_a_due_intent_counts_and_ages(sqlite_session: AsyncSession) -> None:
    """The dead-man switch: this is what grows, unboundedly, while nothing is draining."""
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type)
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.PENDING,
        created_at=_NOW - timedelta(hours=2),
        next_retry_at=_NOW - timedelta(hours=1),
    )
    # `pending` with no send time at all = due immediately (the confirmation email).
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.PENDING,
        created_at=_NOW - timedelta(minutes=10),
        next_retry_at=None,
    )
    # A `failed` intent parked for a backoff retry that has now come round is due as well.
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.FAILED,
        created_at=_NOW - timedelta(minutes=30),
        next_retry_at=_NOW - timedelta(minutes=1),
    )

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.outbox_due == 3
    # Aged from when it BECAME due (its send time), not from when the row was written: a reminder
    # queued three weeks early is not "three weeks late" the instant it comes due.
    assert snapshot.outbox_oldest_due_age_seconds == 3600.0


async def test_a_claimed_row_whose_lease_expired_is_reported(sqlite_session: AsyncSession) -> None:
    """A worker died holding it. The recovery pass returns it to ``pending`` on the next drain —
    unless the drain is what died, in which case this is the number that says so."""
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type)
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.CLAIMED,
        created_at=_NOW - timedelta(minutes=30),
        lease_expires_at=_NOW - timedelta(minutes=5),
    )
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.CLAIMED,
        created_at=_NOW,
        lease_expires_at=_NOW + timedelta(minutes=5),  # healthy, mid-flight
    )

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.outbox_by_status["claimed"] == 2
    assert snapshot.outbox_expired_leases == 1


# --------------------------------------------------------------------------------------
# The no-show rate (RF-25).
# --------------------------------------------------------------------------------------


async def test_the_no_show_rate_is_taken_over_the_appointments_that_should_have_happened(
    sqlite_session: AsyncSession,
) -> None:
    """A cancelled booking was never expected to be attended, so it is not in the denominator.

    Neither is one that has not happened YET — which is exactly what this test used to seed, and
    then assert a rate over. Every booking below was stamped in the FUTURE and the gauge still came
    out at 0.25, because the denominator was ``no_show + confirmed`` with no clock in it at all. The
    appointments are held in the PAST now, and the test finally says what its own name claimed.
    """
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    statuses = [
        BookingStatus.CONFIRMED,
        BookingStatus.CONFIRMED,
        BookingStatus.CONFIRMED,
        BookingStatus.NO_SHOW,
        BookingStatus.CANCELLED,
    ]
    for index, status in enumerate(statuses):
        await _booking(
            sqlite_session,
            tenant,
            event_type,
            status=status,
            start=_NOW - timedelta(days=index + 1),  # all of them are OVER
        )

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.bookings_by_status["no_show"] == 1
    assert snapshot.bookings_by_status["cancelled"] == 1
    assert snapshot.no_show_ratio == 0.25  # 1 / (1 no-show + 3 confirmed that were HELD)


async def test_an_appointment_that_has_not_happened_is_not_in_the_no_show_rate(
    sqlite_session: AsyncSession,
) -> None:
    """==The gauge fell every time a booking was taken.==

    ``confirmed`` is every booking still in the diary, so an instance with one real no-show and a
    week of upcoming appointments reported a no-show rate near ZERO — and an alarm built on that
    gauge would have fired least exactly when the diary was busiest. A rate that improves on its own
    when business is good is not a measurement.
    """
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    await _booking(
        sqlite_session,
        tenant,
        event_type,
        status=BookingStatus.NO_SHOW,
        start=_NOW - timedelta(days=1),
    )
    for day in range(1, 10):  # nine appointments still to come
        await _booking(
            sqlite_session,
            tenant,
            event_type,
            status=BookingStatus.CONFIRMED,
            start=_NOW + timedelta(days=day),
        )

    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.bookings_by_status["confirmed"] == 9  # they ARE on the books...
    assert snapshot.no_show_ratio == 1.0  # ...and not one of them has been attended yet


async def test_the_no_show_rate_of_an_empty_instance_is_zero_not_a_crash(
    sqlite_session: AsyncSession,
) -> None:
    snapshot = await collect_metrics(sqlite_session, now=_NOW)

    assert snapshot.no_show_ratio == 0.0


# --------------------------------------------------------------------------------------
# The drain counters — `lost` is the duplicate-send signal, and it must not drown in noise.
# --------------------------------------------------------------------------------------


def test_the_counters_start_at_zero() -> None:
    counters = DrainCounters()

    assert counters.lost == 0
    assert counters.voided_midflight == 0
    assert counters.passes == 0


def test_a_drain_pass_accumulates_its_report_into_the_counters() -> None:
    counters = DrainCounters()
    report = OutboxReport()
    report.delivered.append(uuid.uuid4())
    report.failed.extend([uuid.uuid4(), uuid.uuid4()])
    report.lost.append(uuid.uuid4())
    report.voided_midflight.extend([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])

    counters.observe(report)
    counters.observe(report)

    assert counters.passes == 2
    assert counters.delivered == 2
    assert counters.failed == 4
    assert counters.lost == 2
    assert counters.voided_midflight == 6


def test_a_routine_cancellation_never_lands_in_the_lost_counter() -> None:
    """``lost`` means a provider call outran its lease, so the guest may have been messaged TWICE.
    ``voided_midflight`` means a cancellation retired a step while it was in flight — routine,
    expected, harmless. Merge them and the one alarm worth waking up for fires every time anybody
    cancels an appointment, which is exactly how it stops being read."""
    counters = DrainCounters()
    report = OutboxReport()
    report.voided_midflight.extend([uuid.uuid4() for _ in range(50)])

    counters.observe(report)

    assert counters.voided_midflight == 50
    assert counters.lost == 0


async def test_draining_the_outbox_feeds_the_counters_without_being_asked(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Wired at the ONE choke point.== Every drain observes itself, so a counter can never read
    zero merely because whoever called the drain forgot to report it."""
    async with sqlite_maker() as session, session.begin():
        tenant, event_type = await _tenant_with_event_type(session)
        booking = await _booking(session, tenant, event_type)
        await _intent(session, booking, status=OutboxStatus.PENDING, created_at=_NOW)

    passes_before = DRAIN_COUNTERS.passes
    delivered_before = DRAIN_COUNTERS.delivered

    async def _execute(work: OutboxWork, now: datetime) -> None:
        return None

    await drain_outbox(_pools(sqlite_maker), now=_NOW, execute=_execute)

    assert DRAIN_COUNTERS.passes == passes_before + 1
    assert DRAIN_COUNTERS.delivered == delivered_before + 1


def test_observe_drain_survives_an_empty_pass() -> None:
    """It runs on every drain, including the overwhelmingly common pass that finds nothing."""
    passes_before = DRAIN_COUNTERS.passes

    observe_drain(OutboxReport())

    assert DRAIN_COUNTERS.passes == passes_before + 1
    assert DRAIN_COUNTERS.lost == DRAIN_COUNTERS.lost  # nothing raised, nothing invented


# --------------------------------------------------------------------------------------
# The Prometheus text — and what it must NEVER carry.
# --------------------------------------------------------------------------------------


async def test_the_exposition_carries_no_per_business_data(sqlite_session: AsyncSession) -> None:
    """==The rule for a PUBLIC repository.== An exposed instance must not leak its customers'
    pipeline. Every series is instance-wide: no tenant id, no slug, no guest, no event title — not
    in a label, not in a value. The test looks for the real values, not for the word "tenant"."""
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type)
    await _intent(sqlite_session, booking, status=OutboxStatus.PENDING, created_at=_NOW)

    text = render_prometheus(
        await collect_metrics(sqlite_session, now=_NOW),
        counters=DrainCounters(),
    )

    for private in (
        tenant.slug,
        str(tenant.id),
        str(booking.id),
        booking.guest_email,
        booking.guest_name,
        event_type.slug,
        event_type.title,
    ):
        assert private not in text


async def test_the_exposition_is_valid_prometheus_text(sqlite_session: AsyncSession) -> None:
    tenant, event_type = await _tenant_with_event_type(sqlite_session)
    booking = await _booking(sqlite_session, tenant, event_type, status=BookingStatus.NO_SHOW)
    await _intent(
        sqlite_session,
        booking,
        status=OutboxStatus.PENDING,
        created_at=_NOW - timedelta(minutes=5),
        next_retry_at=_NOW - timedelta(minutes=5),
    )
    counters = DrainCounters()
    report = OutboxReport()
    report.lost.append(uuid.uuid4())
    counters.observe(report)

    text = render_prometheus(await collect_metrics(sqlite_session, now=_NOW), counters=counters)
    lines = text.splitlines()

    assert "# HELP aethercal_outbox_intents Outbox intents by status." in lines
    assert "# TYPE aethercal_outbox_intents gauge" in lines
    assert 'aethercal_outbox_intents{status="pending"} 1' in lines
    assert "aethercal_outbox_due 1" in lines
    assert "aethercal_outbox_oldest_due_age_seconds 300" in lines
    # THE one to alert on: a send that outran its lease, i.e. a possible duplicate delivery.
    assert "aethercal_outbox_drain_lost_total 1" in lines
    assert 'aethercal_bookings{status="no_show"} 1' in lines
    for line in lines:
        if line.startswith("#") or not line:
            continue
        _, _, value = line.rpartition(" ")
        float(value)  # a sample whose value is not a number is not Prometheus text


async def test_the_scheduler_enabled_gauge_is_gone_with_the_process_that_needed_it(
    sqlite_session: AsyncSession,
) -> None:
    """==A gauge whose only job was to warn you that you were reading the wrong process.==

    The drain counters are PROCESS-local. Published from the WEB process — which is where
    ``/metrics`` used to live — they could only ever read zero, and a zero there is
    indistinguishable

    ``/metrics`` is served by ``aethercal-worker`` now, which IS the drain: the counters are true
    where they are published, and there is no longer any such thing as a scrape of a process that
    does not drain. So the warning gauge goes with the problem it described. Leaving it behind would
    have meant a series that is always ``1`` — noise pretending to be a signal.
    """
    text = render_prometheus(
        await collect_metrics(sqlite_session, now=_NOW), counters=DrainCounters()
    )

    # The gauges that MATTER are read from the database and are correct from any process — which is
    # precisely why the dead-man switch was built on them, and not on the counters.
    assert "aethercal_outbox_oldest_due_age_seconds" in text
    assert "aethercal_outbox_due" in text


def test_render_human_names_the_sections_and_the_health_lines() -> None:
    """The human summary a person reads: the operational sections and the three lines that decide
    whether the shadow is healthy, each called out by name rather than buried in a gauge."""
    snapshot = MetricsSnapshot(
        outbox_by_status={"pending": 2, "delivered": 5, "skipped": 1},
        outbox_due=3,
        outbox_oldest_due_age_seconds=42.0,
        bookings_by_status={"confirmed": 4, "no_show": 1, "cancelled": 2},
        no_show_ratio=0.2,
        webhook_deliveries_by_status={"delivered": 6},
        webhook_deliveries_by_reason={"blocked-private-target": 0, "http-error": 1},
        payment_events_parked=0,
        payment_events_dead=0,
    )

    report = render_human(snapshot, now=_NOW)

    assert "Bookings" in report
    assert "Outbox" in report
    assert "Webhooks" in report
    assert "Payments" in report
    # The no-show rate reads as a percentage over the appointments that were meant to happen.
    assert "20.0%" in report
    # The three named health lines.
    assert "dead-man" in report  # the outbox oldest-due
    assert "blocked-*" in report  # the webhook refusal
    assert "MONEY dead-man" in report  # the payment dead-letter


def test_render_human_shows_zeroes_rather_than_hiding_an_absent_series() -> None:
    """An empty instance still prints every section with (none)/0 — "absent" and "healthy zero" must
    never look the same to the operator, the same rule the Prometheus gauges hold."""
    report = render_human(MetricsSnapshot(), now=_NOW)

    assert "Bookings" in report
    assert "(none)" in report  # the empty status maps, spelled out, not omitted
    assert "0.0%" in report
