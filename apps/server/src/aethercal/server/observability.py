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
  the drain accumulates them as it runs. They used to be published by the WEB process, where they
  could only ever read zero — a zero indistinguishable from "nothing was ever lost", which is why
  the exposition once carried an ``aethercal_scheduler_enabled`` gauge to warn you that you were
  reading the wrong process. ``GET /metrics`` now lives in the ``aethercal-worker`` process, which
  IS the drain, so the counters are finally true where they are published and that warning gauge is
  gone with the problem it described.

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
from aethercal.server.db.models import (
    Booking,
    Outbox,
    OutboxStatus,
    PaymentEvent,
    PaymentEventStatus,
    WebhookDelivery,
)
from aethercal.server.db.models.booking import held_filter
from aethercal.server.db.models.outbox import due_filter
from aethercal.server.db.pools import require_bypass
from aethercal.server.webhooks.delivery import DELIVERY_FAILURE_REASONS

if TYPE_CHECKING:  # pragma: no cover - the drain imports THIS module, so the type is deferred
    from aethercal.server.services.outbox import OutboxReport

_logger = logging.getLogger(__name__)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
"""The Prometheus text-exposition content type (the format Prometheus scrapes by default)."""

_WEBHOOK_STATUSES = ("pending", "delivered", "failed", "dead")
"""The lifecycle of a ``webhook_deliveries`` row. Every one gets a gauge, zeroes included."""


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
    purged_midflight: int = 0
    """Intents a GUEST ERASURE deleted WHILE they were being sent. Routine, expected, NOT lost.

    The third cause of a vanished row, kept out of :attr:`lost` for exactly the reason
    :attr:`voided_midflight` is: an alarm that also fires on every erasure that happens to land
    during a drain is an alarm nobody reads."""
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
        self.purged_midflight += len(report.purged_midflight)
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
    webhook_deliveries_by_status: dict[str, int] = field(default_factory=dict)
    """Outbound webhook deliveries by status. The webhook worker had NO instrumentation at all."""
    webhook_deliveries_by_reason: dict[str, int] = field(default_factory=dict)
    """Undelivered webhooks by WHY — every member of ``DELIVERY_FAILURE_REASONS``, zeroes included.

    ==This is the number that makes the silent failure visible.== A self-hoster whose n8n is on the
    LAN, with no allowlist declared, sees ``blocked-private-target`` climb — and the fix is one
    environment variable away. Before it existed, that instance was indistinguishable from a healthy
    one: no error, no metric, no log, and no events.

    ``blocked-*`` is worth an alarm; ``http-error`` mostly is not (a consumer having a bad day is
    ordinary). Keeping them in one series with a bounded ``reason`` label — never a URL, never a
    tenant — is what lets a dashboard tell those apart on a public, scrapeable endpoint."""
    payment_events_parked: int = 0
    """Inbound payment events whose booking does not exist yet, awaiting a retry (B-05b). A backlog
    that grows and never drains means the parked-payment tick is not running."""
    payment_events_dead: int = 0
    """==THE money dead-man switch.== Parked payment events retried to exhaustion: each is a charge
    that neither confirmed nor refunded — by this spec, the worst outcome the system can produce.
    Any non-zero value is a human task, never routine."""


async def collect_metrics(session: AsyncSession, *, now: datetime) -> MetricsSnapshot:
    """Read the instance's operational state. Read-only, instance-wide, no tenant dimension.

    ==It REFUSES a session that is not on the bypass pool. It fails; it does not degrade.==

    Count the ``WHERE tenant_id`` clauses in the queries below: there are none, and there are not
    supposed to be — this is the OPERATOR's view of every business at once. But that also means
    every one of them is a cross-business query, and under row-level security a cross-business query
    on the app role does not raise. It returns nothing. And this function is written to FILL WITH
    ZEROS wherever it finds nothing (``dict.fromkeys(..., 0)``, ``or 0``, ``if expected else 0.0``),
    because for a dashboard an absent series is worse than a zero one.

    Put those two facts together and the result is the worst outcome available: ``outbox.due = 0``,
    ``oldest_due_age_seconds = 0.0``, ``status: "ready"`` — **for ever, with the outbox on fire.**
    The one component whose entire job is to make a dead drain visible would have become the deadest
    thing in the system, and it would have been the isolation batch that killed it.

    So the belt goes at the ROOT rather than on each caller: a session with no bypass marker is a
    hard error here. That is what stops the next ``/health/ready`` — an unauthenticated,
    cross-business, zero-filling endpoint that nobody thought of as a metrics consumer — from ever
    being born.
    """
    require_bypass(session, caller="collect_metrics")

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

    webhooks_by_status: dict[str, int] = dict.fromkeys(_WEBHOOK_STATUSES, 0)
    for status, count in await session.execute(
        sa.select(WebhookDelivery.status, sa.func.count()).group_by(WebhookDelivery.status)
    ):
        webhooks_by_status[status] = count

    # Every reason gets a series, whether or not any row carries it: a dashboard cannot alert on a
    # series that does not exist, and "absent" must never be mistaken for "zero".
    webhooks_by_reason: dict[str, int] = dict.fromkeys(DELIVERY_FAILURE_REASONS, 0)
    for reason, count in await session.execute(
        sa.select(WebhookDelivery.error_reason, sa.func.count())
        .where(WebhookDelivery.error_reason.is_not(None))
        .group_by(WebhookDelivery.error_reason)
    ):
        if reason not in webhooks_by_reason:
            # A token the table holds but the code does not know is a real divergence — an older
            # binary, or a reason somebody added without extending DELIVERY_FAILURE_REASONS. Say so
            # rather than dropping those rows out of a gauge that then reads reassuringly low.
            _logger.error(
                "webhook_deliveries holds error_reason %r, which is not a known delivery reason; "
                "it is not accounted for in the metrics",
                reason,
            )
            continue
        webhooks_by_reason[reason] = count

    # The appointments that already SHOULD have happened — never "no_show + confirmed", which is
    # what this counted before. ``confirmed`` is every booking still in the diary, so an instance
    # with one real no-show and ninety-nine appointments next week reported a 1 % rate; worse, the
    # FELL every time a booking was taken, so an alarm built on it would fire least exactly when the
    # diary was busiest. The predicate has ONE owner now (``held_filter``), because this module and
    # the admin's health panel both publish this number and had already drifted into the same error.
    held = held_filter(now)
    expected = (
        await session.scalar(sa.select(sa.func.count()).select_from(Booking).where(held))
    ) or 0
    absent = (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Booking)
            .where(held, Booking.status == BookingStatus.NO_SHOW.value)
        )
    ) or 0

    # The money dead-man switch (B-05b): parked events awaiting retry, and the dead-lettered ones —
    # a charge that neither confirmed nor refunded. Cross-tenant, like everything else here.
    def _payment_events_in(status: PaymentEventStatus) -> sa.ScalarSelect[int]:
        return (
            sa.select(sa.func.count())
            .select_from(PaymentEvent)
            .where(PaymentEvent.status == status.value)
            .scalar_subquery()
        )

    parked_dead = (
        await session.execute(
            sa.select(
                _payment_events_in(PaymentEventStatus.PARKED),
                _payment_events_in(PaymentEventStatus.DEAD),
            )
        )
    ).one()

    return MetricsSnapshot(
        outbox_by_status=by_status,
        outbox_due=outbox_due,
        outbox_oldest_due_age_seconds=_age_seconds(oldest_due, now=now),
        outbox_expired_leases=expired_leases,
        bookings_by_status=bookings,
        no_show_ratio=(absent / expected) if expected else 0.0,
        webhook_deliveries_by_status=webhooks_by_status,
        webhook_deliveries_by_reason=webhooks_by_reason,
        payment_events_parked=parked_dead[0] or 0,
        payment_events_dead=parked_dead[1] or 0,
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


def render_prometheus(snapshot: MetricsSnapshot, *, counters: DrainCounters) -> str:
    """Render the exposition. ==Instance-wide only: no tenant, no guest, no event title.==

    ``scheduler_enabled`` is gone, and with it the question it answered. It existed because
    ``/metrics`` was served by the WEB process, where the drain counters below are process-local and
    therefore read zero — a zero that looked exactly like "nothing was ever lost". The endpoint now
    lives in the ``aethercal-worker`` process, which IS the drain: the counters are true where they
    are published, and there is no longer any such thing as a scrape of a process that does not
    drain. A gauge whose only purpose was to warn you that you were reading the wrong process is a
    gauge that should disappear when the wrong process does.
    """
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
        "aethercal_outbox_drain_purged_midflight_total",
        "Intents deleted by a guest erasure while in flight. Routine and expected; counted apart "
        "from 'lost' so that alarm is not drowned by every ordinary right-to-be-forgotten request.",
        "counter",
        [("", counters.purged_midflight)],
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
    _series(
        lines,
        "aethercal_webhook_deliveries",
        "Outbound webhook deliveries by status.",
        "gauge",
        [
            (f'{{status="{_escape(status)}"}}', count)
            for status, count in sorted(snapshot.webhook_deliveries_by_status.items())
        ],
    )
    _series(
        lines,
        "aethercal_webhook_deliveries_failed",
        "Undelivered webhooks by REASON. Alert on the 'blocked-*' reasons: they mean this instance "
        "REFUSED to send, and 'blocked-private-target' on a self-host almost always means the "
        "operator's own service (n8n/CRM/ERP) is on a network they have not declared in "
        "AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS — the events are not late, they are never coming. "
        "'http-error' and 'transport-error' are the ordinary bad day of a consumer.",
        "gauge",
        [
            (f'{{reason="{_escape(reason)}"}}', count)
            for reason, count in sorted(snapshot.webhook_deliveries_by_reason.items())
        ],
    )
    _series(
        lines,
        "aethercal_payment_events_parked",
        "Inbound payment events awaiting a booking that does not exist yet (B-05b). A backlog that "
        "grows and never drains means the parked-payment tick is not running.",
        "gauge",
        [("", snapshot.payment_events_parked)],
    )
    _series(
        lines,
        "aethercal_payment_events_dead",
        "Parked payment events retried to exhaustion: each is a charge that neither confirmed nor "
        "refunded — the worst outcome the system can produce. ANY non-zero value is a human task.",
        "gauge",
        [("", snapshot.payment_events_dead)],
    )
    return "\n".join(lines) + "\n"


def _sorted_counts(counts: dict[str, int]) -> str:
    """``a=1, b=0, c=3`` — every key, zeroes included, so "absent" and "zero" never look alike."""
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "(none)"


def render_human(snapshot: MetricsSnapshot, *, now: datetime) -> str:
    """A readable operational summary — the same numbers as :func:`render_prometheus`, for a person.

    ``/metrics`` answers a scraper; this answers the operator running the shadow, who would
    otherwise read the instance's health by opening psql. It is DB-derived and instance-wide (never
    per-tenant), so it is safe to print: it names statuses and counts, never a guest, a URL, or a
    business.

    The three lines that decide whether the shadow is healthy, called out by name:

    * ``outbox oldest-due`` — the dead-man switch. Flat means the drain is keeping up; unbounded
      growth means nothing is draining, and every reminder is quietly late.
    * ``webhook blocked-*`` — the CRM bridge REFUSING to send. On a self-host it usually means the
      operator's own CRM is on a network they have not declared; the events are not late, they are
      never coming.
    * ``payments dead`` — the money dead-man. Each is a charge that neither confirmed nor refunded.
      Any non-zero value is a human task, never routine.
    """
    total_appts = snapshot.bookings_by_status.get("confirmed", 0) + snapshot.bookings_by_status.get(
        "no_show", 0
    )
    return "\n".join(
        [
            f"AetherCal — operational snapshot @ {now.isoformat(timespec='seconds')}",
            "(instance-wide, DB-derived, no guest/tenant data)",
            "",
            "Bookings",
            f"  by status:       {_sorted_counts(snapshot.bookings_by_status)}",
            f"  no-show rate:    {snapshot.no_show_ratio * 100:.1f}%  "
            f"(of {total_appts} appointment(s) that were meant to happen; cancelled excluded)",
            "",
            "Outbox (the send queue)",
            f"  by status:       {_sorted_counts(snapshot.outbox_by_status)}",
            f"  due (overdue):   {snapshot.outbox_due}",
            f"  oldest due:      {snapshot.outbox_oldest_due_age_seconds:.0f}s"
            "   <- dead-man switch: flat = healthy, climbing = nothing is draining",
            f"  expired leases:  {snapshot.outbox_expired_leases}",
            "",
            "Webhooks (CRM bridge)",
            f"  by status:       {_sorted_counts(snapshot.webhook_deliveries_by_status)}",
            f"  failed by reason:{_sorted_counts(snapshot.webhook_deliveries_by_reason)}"
            "   <- alert on any blocked-*",
            "",
            "Payments",
            f"  parked:          {snapshot.payment_events_parked}  (awaiting their booking)",
            f"  dead:            {snapshot.payment_events_dead}"
            "   <- MONEY dead-man: any non-zero is a human task",
            "",
        ]
    )


__all__ = [
    "CONTENT_TYPE",
    "DRAIN_COUNTERS",
    "DrainCounters",
    "MetricsSnapshot",
    "collect_metrics",
    "observe_drain",
    "render_human",
    "render_prometheus",
]
