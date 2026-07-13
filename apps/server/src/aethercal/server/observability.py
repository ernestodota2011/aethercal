"""R9 — the instrumentation that makes a DEAD SCHEDULER visible.

The hole, stated plainly: **if the drain process dies, the emails stop going out and nothing reports
it.** Every booking still confirms, every intent is still queued, every test still passes, and no
guest ever hears from the system again. It is this project's signature defect — the silent no-op —
aimed squarely at the one component nobody watches.

.. rubric:: What actually detects it, and what merely looks like it does

The instinct is to alarm on ``pending``. That is the wrong number. The outbox **is** the durable
scheduler, so a 24 h reminder for a booking three weeks out sits ``pending`` for three weeks and is
in perfect health. An alarm on ``pending`` fires on a healthy instance, so it gets silenced — and a
silenced alarm is not an alarm.

The number that stays flat on a healthy instance and grows without bound the moment nothing drains
is the **DUE** backlog — the intents whose send time has passed and which are still not delivered —
and above all :attr:`MetricsSnapshot.outbox_oldest_due_age_seconds`, *how long the oldest of them
has been waiting*. That is the dead-man switch, and it is read straight from the DATABASE, so it is
true no matter which process answers the scrape.

.. rubric:: Two kinds of number, and the honest difference between them

* **DB-derived gauges** (the backlog, the due backlog, the oldest-due age, the expired leases, the
  booking counts) are read from the table on every scrape. Correct from ANY process.
* **Drain counters** (``lost``, ``voided_midflight``, delivered/failed/dead/…) are PROCESS-local:
  the drain accumulates them as it runs, and the container starts the scheduler in exactly one
  process. Scrape a different one and they read zero — which looks exactly like "nothing was ever
  lost". Rather than pretend otherwise, the exposition publishes ``aethercal_scheduler_enabled`` so
  an operator can tell a healthy zero from a meaningless one.

  ``lost`` is the one to alert on: it means a provider call outran its lease, the row was recovered
  under the worker, and the effect may have been executed **twice** — a duplicate message to a real
  guest. Which is exactly why a step retired mid-flight by a routine cancellation is counted
  separately as ``voided_midflight``: an alarm that also fires on every ordinary cancellation is an
  alarm nobody reads.

.. rubric:: This is a PUBLIC repository

An exposed instance must never leak its customers' pipeline, so **no series carries per-business
data** — no tenant id, no slug, no guest, no event title; not in a label, not in a value. Everything
here is instance-wide, and ``GET /metrics`` is authenticated with an OPERATOR token that is not, and
cannot be, a tenant's API key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import Booking, Outbox, OutboxStatus
from aethercal.server.db.models.outbox import due_filter

if TYPE_CHECKING:  # pragma: no cover - the drain imports THIS module, so the type is deferred
    from aethercal.server.services.outbox import OutboxReport

_logger = logging.getLogger(__name__)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
"""The Prometheus text-exposition content type (the format Prometheus scrapes by default)."""


# --------------------------------------------------------------------------------------
# The process-local drain counters.
# --------------------------------------------------------------------------------------


@dataclass
class DrainCounters:
    """Monotonic counters accumulated by the outbox drain, in THIS process.

    Not persisted: the alternative is a counters table, and this batch's contract is one migration
    with one owner. The durable, cross-process signal is the DB-derived backlog below — these
    counters are the finer-grained story of what the drain did while it was alive.
    """

    passes: int = 0
    delivered: int = 0
    failed: int = 0
    dead: int = 0
    deferred: int = 0
    skipped: int = 0
    recovered: int = 0
    unclaimed: int = 0
    voided_midflight: int = 0
    """Steps a booking transition retired WHILE they were being sent. Routine, expected, NOT lost.

    Kept out of :attr:`lost` deliberately — that separation is the whole reason ``lost`` stays worth
    alerting on."""
    lost: int = 0
    """==Sends whose LEASE EXPIRED mid-flight, so the result was discarded: the effect may have run
    TWICE.==

    A non-zero value means a provider call outran ``PROVIDER_TIMEOUT_CEILING`` in spite of the
    ceiling, another worker took the row over, and a real guest may have been messaged twice. It is
    the single most important number this module publishes."""

    def observe(self, report: OutboxReport) -> None:
        """Fold one drain pass's report in. Called for EVERY pass, including the empty ones."""
        self.passes += 1
        self.delivered += len(report.delivered)
        self.failed += len(report.failed)
        self.dead += len(report.dead)
        self.deferred += len(report.deferred)
        self.skipped += len(report.skipped)
        self.recovered += len(report.recovered)
        self.unclaimed += len(report.unclaimed)
        self.voided_midflight += len(report.voided_midflight)
        self.lost += len(report.lost)
        if report.lost:
            _logger.error(
                "outbox drain: %d intent(s) LOST their lease mid-flight this pass; each may have "
                "been delivered twice. aethercal_outbox_drain_lost_total is now %d",
                len(report.lost),
                self.lost,
            )


DRAIN_COUNTERS = DrainCounters()
"""This process's counters. There is exactly one, and the drain feeds it itself."""


def observe_drain(report: OutboxReport) -> None:
    """Record one drain pass. Called from INSIDE ``drain_outbox`` — the single choke point.

    Wired into the drain rather than into the scheduler tick on purpose: a counter that only moves
    when the CALLER remembers to report it reads zero for the one code path somebody forgot, and a
    zero meaning "never measured" is indistinguishable from a zero meaning "nothing went wrong"."""
    DRAIN_COUNTERS.observe(report)


# --------------------------------------------------------------------------------------
# The DB-derived snapshot.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """One read of the instance's operational state. Instance-wide; never per-tenant."""

    outbox_by_status: dict[str, int] = field(default_factory=dict)
    """Every member of :class:`OutboxStatus`, whether or not it has rows — a dashboard cannot alert
    on a series that does not exist, so "absent" and "zero" must never look the same."""
    outbox_due: int = 0
    """Intents whose send time has PASSED and which are still undelivered. ==The alertable one.=="""
    outbox_oldest_due_age_seconds: float = 0.0
    """How long the oldest DUE intent has been waiting. ==The dead-man switch.== Flat on a healthy
    instance; unbounded growth the moment nothing is draining."""
    outbox_expired_leases: int = 0
    """``claimed`` rows whose lease has elapsed: a worker died holding them. The recovery pass hands
    them back on the next drain — unless the drain is what died."""
    bookings_by_status: dict[str, int] = field(default_factory=dict)
    no_show_ratio: float = 0.0
    """No-shows over the appointments that were SUPPOSED to happen (RF-25).

    Cancelled bookings are not in the denominator: nobody was ever expected to attend them, and
    counting them would make a host's no-show rate improve simply because more people cancelled."""


async def collect_metrics(session: AsyncSession, *, now: datetime) -> MetricsSnapshot:
    """Read the instance's operational state. Read-only, instance-wide, no tenant dimension."""
    by_status = {status.value: 0 for status in OutboxStatus}
    for status, count in await session.execute(
        sa.select(Outbox.status, sa.func.count()).group_by(Outbox.status)
    ):
        if status not in by_status:
            # A status the table holds but the enum does not know is a real divergence, not a
            # rounding error. Say so, loudly, instead of quietly dropping those rows out of a
            # backlog gauge that then reads reassuringly low.
            _logger.error(
                "the outbox holds rows with status %r, which is not a member of OutboxStatus; the "
                "backlog gauges cannot account for them",
                status,
            )
            continue
        by_status[status] = count

    due_at = sa.func.coalesce(Outbox.next_retry_at, Outbox.created_at)
    outbox_due = (
        await session.scalar(sa.select(sa.func.count()).select_from(Outbox).where(due_filter(now)))
    ) or 0
    oldest_due = await session.scalar(
        sa.select(due_at).where(due_filter(now)).order_by(due_at.asc()).limit(1)
    )
    expired_leases = (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Outbox)
            .where(
                Outbox.status == OutboxStatus.CLAIMED.value,
                Outbox.lease_expires_at.is_not(None),
                Outbox.lease_expires_at <= now,
            )
        )
    ) or 0

    bookings = {status.value: 0 for status in BookingStatus}
    for status, count in await session.execute(
        sa.select(Booking.status, sa.func.count()).group_by(Booking.status)
    ):
        bookings[BookingStatus(status).value] = count

    attended = bookings[BookingStatus.NO_SHOW.value] + bookings[BookingStatus.CONFIRMED.value]
    return MetricsSnapshot(
        outbox_by_status=by_status,
        outbox_due=outbox_due,
        outbox_oldest_due_age_seconds=_age_seconds(oldest_due, now=now),
        outbox_expired_leases=expired_leases,
        bookings_by_status=bookings,
        no_show_ratio=(bookings[BookingStatus.NO_SHOW.value] / attended) if attended else 0.0,
    )


def _age_seconds(moment: datetime | None, *, now: datetime) -> float:
    """Seconds since ``moment``, never negative. ``None`` — nothing is due — is ``0``.

    SQLite hands timestamps back naive, so normalise before doing arithmetic: subtracting a naive
    from an aware datetime raises, and a metrics endpoint that 500s during an incident is worse than
    no metrics endpoint at all."""
    if moment is None:
        return 0.0
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return max((now - aware).total_seconds(), 0.0)


# --------------------------------------------------------------------------------------
# The Prometheus text exposition — hand-rolled: it is a dozen lines, and it adds no dependency.
# --------------------------------------------------------------------------------------


def _num(value: float) -> str:
    """Render a sample value: ``1``, not ``1.0`` — but ``0.25`` stays ``0.25``."""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(round(float(value), 6))


def _escape(label_value: str) -> str:
    """Escape a label value per the exposition format.

    Every label emitted here is an enum member today, so nothing needs escaping — which is exactly
    why it is done anyway. The first person to add a label carrying a name will not remember to, and
    a stray quote or newline silently corrupts the whole scrape."""
    return label_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _series(
    lines: list[str], name: str, help_text: str, kind: str, samples: list[tuple[str, float]]
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {kind}")
    lines.extend(f"{name}{labels} {_num(value)}" for labels, value in samples)


def render_prometheus(
    snapshot: MetricsSnapshot, *, counters: DrainCounters, scheduler_enabled: bool
) -> str:
    """Render the exposition. ==Instance-wide only: no tenant, no guest, no event title.=="""
    lines: list[str] = []

    _series(
        lines,
        "aethercal_outbox_intents",
        "Outbox intents by status.",
        "gauge",
        [
            (f'{{status="{_escape(status)}"}}', count)
            for status, count in sorted(snapshot.outbox_by_status.items())
        ],
    )
    _series(
        lines,
        "aethercal_outbox_due",
        "Intents whose send time has passed and which are still undelivered. Alert on THIS, never "
        "on 'pending': a reminder queued weeks ahead is pending and perfectly healthy.",
        "gauge",
        [("", snapshot.outbox_due)],
    )
    _series(
        lines,
        "aethercal_outbox_oldest_due_age_seconds",
        "How long the oldest due intent has been waiting. The dead-man switch: flat on a healthy "
        "instance, unbounded growth the moment nothing is draining.",
        "gauge",
        [("", snapshot.outbox_oldest_due_age_seconds)],
    )
    _series(
        lines,
        "aethercal_outbox_expired_leases",
        "Claimed intents whose lease has elapsed: a drain worker died holding them.",
        "gauge",
        [("", snapshot.outbox_expired_leases)],
    )
    _series(
        lines,
        "aethercal_outbox_drain_lost_total",
        "Sends whose lease expired mid-flight, so the worker's result was discarded: the effect "
        "MAY HAVE RUN TWICE (a duplicate message to a real guest). Non-zero wants a look.",
        "counter",
        [("", counters.lost)],
    )
    _series(
        lines,
        "aethercal_outbox_drain_voided_midflight_total",
        "Steps retired by a booking transition while in flight. Routine and expected; counted "
        "apart from 'lost' so that alarm is not drowned by every ordinary cancellation.",
        "counter",
        [("", counters.voided_midflight)],
    )
    _series(
        lines,
        "aethercal_outbox_drain_intents_total",
        "Outcomes recorded by this process's drain passes.",
        "counter",
        [
            ('{outcome="delivered"}', counters.delivered),
            ('{outcome="failed"}', counters.failed),
            ('{outcome="dead"}', counters.dead),
            ('{outcome="deferred"}', counters.deferred),
            ('{outcome="skipped"}', counters.skipped),
            ('{outcome="recovered"}', counters.recovered),
            ('{outcome="unclaimed"}', counters.unclaimed),
        ],
    )
    _series(
        lines,
        "aethercal_outbox_drain_passes_total",
        "Drain passes run by THIS process.",
        "counter",
        [("", counters.passes)],
    )
    _series(
        lines,
        "aethercal_scheduler_enabled",
        "1 if this process runs the drain. The drain counters above are process-local, so on a "
        "process reporting 0 they are meaningless rather than reassuring; the backlog gauges are "
        "read from the database and are correct anywhere.",
        "gauge",
        [("", int(scheduler_enabled))],
    )
    _series(
        lines,
        "aethercal_bookings",
        "Bookings by status.",
        "gauge",
        [
            (f'{{status="{_escape(status)}"}}', count)
            for status, count in sorted(snapshot.bookings_by_status.items())
        ],
    )
    _series(
        lines,
        "aethercal_bookings_no_show_ratio",
        "No-shows over the appointments that were supposed to happen (no-show + confirmed). "
        "Cancelled bookings are excluded: nobody was ever expected to attend them.",
        "gauge",
        [("", snapshot.no_show_ratio)],
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "CONTENT_TYPE",
    "DRAIN_COUNTERS",
    "DrainCounters",
    "MetricsSnapshot",
    "collect_metrics",
    "observe_drain",
    "render_prometheus",
]
