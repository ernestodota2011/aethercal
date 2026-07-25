"""The three phases: organic two-week load, adversarial concurrency, and the controls.

.. rubric:: Why the controls are not an appendix

A run that only ever books successfully proves nothing about double-booking, because it never asked
the question. ==Every positive claim in the report has a control that must FAIL, and if a control
comes back green the run is void rather than excellent.== That is the whole difference between a
measurement and an anecdote.

Two of the controls audit the *harness* rather than the product, and they are the ones worth naming:

* **The distinct-slot race** (:func:`race_distinct_slots`) fires the same N-way simultaneous burst
  through the same code path as the same-slot race, but at N DIFFERENT slots — and demands N
  winners. Without it, "exactly one winner" is not evidence of anything: a harness that had silently
  serialised its threads, or that counted winners wrongly, reports exactly one winner *whatever the
  product does*. This is the control that tells "the product refused 39 requests" apart from "the
  harness only ever really sent one".
* **The drain dead-man** (:func:`control_drain_deadman`) strands real work by stopping the worker,
  then restarts it and demands that the metric SEE the stranded work and then clear it. Without it,
  "peak backlog: 3" is unfalsifiable: an instrument wired to a constant zero draws a beautifully
  flat graph, which is exactly the failure ``api/operator.py`` was moved out of the web process to
  prevent. Note it does NOT read the backlog *during* the outage - it cannot, and that limitation
  is itself a finding the control documents.

.. rubric:: Simultaneity

The races use :class:`threading.Barrier`, so N threads block until all N are ready and are then
released together. Firing requests in a loop — even a tight one — staggers them by however long a
request takes to build, which on a fast local stack is easily enough for the first to commit before
the last is sent. That is not a race; it is a queue, and it would pass while proving nothing.
"""

from __future__ import annotations

import base64
import json
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .client import Client, Response
from .measure import ErrorTally, Latency, OutboxSampler, OutboxScrapeError, record
from .traffic import PlannedBooking
from .world import Business, StackConfig, World

#: A contender that never reaches the barrier would hang every other thread for ever. A run that
#: fails is recoverable; a run that hangs at 3am is not.
_BARRIER_TIMEOUT_SECONDS = 60.0


# --------------------------------------------------------------------------------------
# Small helpers over the API
# --------------------------------------------------------------------------------------


def fetch_slots(
    client: Client, *, event_type_id: str, day: date, timezone: str, days: int = 1
) -> Response:
    end = day + timedelta(days=days - 1)
    query = f"event_type={event_type_id}&from={day.isoformat()}&to={end.isoformat()}&tz={timezone}"
    return client.get(f"/api/v1/slots/?{query}")


def slot_starts(response: Response) -> list[str]:
    body: Any = response.body
    if not isinstance(body, dict):
        return []
    slots: Any = body.get("slots", [])
    if not isinstance(slots, list):
        return []
    return [str(slot["start"]) for slot in slots if isinstance(slot, dict) and "start" in slot]


def book(  # noqa: PLR0913 - the booking contract IS these fields; bundling them hides the payload
    client: Client,
    *,
    event_type_id: str,
    start: str,
    guest_name: str,
    guest_email: str,
    guest_timezone: str,
    locale: str,
) -> Response:
    return client.post(
        "/api/v1/bookings/",
        {
            "event_type_id": event_type_id,
            "start": start,
            "guest_name": guest_name,
            "guest_email": guest_email,
            "guest_timezone": guest_timezone,
            "locale": locale,
        },
    )


@dataclass(frozen=True, slots=True)
class BookedRef:
    """A booking the run really created, and the wall-clock instant its 201 came back.

    ``created_at_wall`` is :func:`time.time`, not a monotonic counter, because it is subtracted from
    Mailpit's own ``Created`` timestamp to measure drain latency. Both are read on the same host, so
    they share one clock.
    """

    booking_id: str
    business_slug: str
    guest_email: str
    start: str
    created_at_wall: float


# --------------------------------------------------------------------------------------
# Phase 1 — organic two-week load
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class OrganicResult:
    booked: list[BookedRef] = field(default_factory=list)
    slots_latency: Latency = field(default_factory=lambda: Latency("slots_read"))
    booking_latency: Latency = field(default_factory=lambda: Latency("booking_create"))
    cancel_latency: Latency = field(default_factory=lambda: Latency("booking_cancel"))
    reschedule_latency: Latency = field(default_factory=lambda: Latency("booking_reschedule"))
    tally: ErrorTally = field(default_factory=ErrorTally)
    collisions: int = 0
    """Organic ``slot_unavailable`` refusals — two simulated guests wanting the same time."""
    no_slots_offered: int = 0
    """Attempts that met an empty offer (a fully booked day). Not an error; reported separately."""
    cancelled: int = 0
    rescheduled: int = 0


def run_organic(
    world: World,
    stack: StackConfig,
    plan: list[PlannedBooking],
    *,
    workers: int,
    seed: int,
) -> OrganicResult:
    """Execute the plan with ``workers`` simulated guests booking at the same time.

    Each guest does what the booking page does: read the day's offer, choose from it, then book. The
    read is measured too — it is the hottest path in the product and the one a page load pays for.

    ==Concurrency here is realistic, not adversarial.== Guests choose randomly among the offered
    slots, so collisions occur at the rate crowding produces rather than by construction; the
    deliberate collisions are Phase 2's job. Organic ``slot_unavailable`` refusals are counted and
    reported as their own number precisely so the two are never conflated.
    """
    result = OrganicResult()
    lock = threading.Lock()
    clients = {b.slug: Client(stack.api_url, b.config.api_key) for b in world.businesses}

    def one(item: PlannedBooking) -> None:
        business = world.by_slug(item.business_slug)
        client = clients[item.business_slug]
        event_type = business.event_types[item.event_key]

        slots_response = record(
            result.slots_latency,
            result.tally,
            fetch_slots(
                client, event_type_id=event_type.id, day=item.day, timezone=item.guest_timezone
            ),
        )
        starts = slot_starts(slots_response)
        if not starts:
            with lock:
                result.no_slots_offered += 1
            return

        # Seeded per booking, not per thread: which slot a guest picks stays deterministic no matter
        # which worker happens to run them or in what order.
        rng = random.Random(seed * 1_000_003 + item.seq)
        start = rng.choice(starts)
        response = record(
            result.booking_latency,
            result.tally,
            book(
                client,
                event_type_id=event_type.id,
                start=start,
                guest_name=item.guest_name,
                guest_email=item.guest_email,
                guest_timezone=item.guest_timezone,
                locale=item.locale,
            ),
        )
        created_at = time.time()
        if response.error_code == "slot_unavailable":
            with lock:
                result.collisions += 1
            return
        if not response.ok:
            return
        body: Any = response.body
        with lock:
            result.booked.append(
                BookedRef(
                    booking_id=str(body["id"]),
                    business_slug=item.business_slug,
                    guest_email=item.guest_email,
                    start=str(body["start"]),
                    created_at_wall=created_at,
                )
            )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="guest") as pool:
        list(pool.map(one, plan))

    # ---- follow-ups: the cancellations and reschedules the plan called for ----
    by_email = {ref.guest_email: ref for ref in result.booked}
    follow_ups = [
        item for item in plan if item.follow_up != "none" and item.guest_email in by_email
    ]

    def follow_up(item: PlannedBooking) -> None:
        ref = by_email[item.guest_email]
        business = world.by_slug(item.business_slug)
        client = clients[item.business_slug]
        if item.follow_up == "cancel":
            response = record(
                result.cancel_latency,
                result.tally,
                client.post(f"/api/v1/bookings/{ref.booking_id}/cancel"),
            )
            if response.ok:
                with lock:
                    result.cancelled += 1
            return

        event_type = business.event_types[item.event_key]
        offer = fetch_slots(
            client, event_type_id=event_type.id, day=item.day, timezone=item.guest_timezone, days=3
        )
        starts = [start for start in slot_starts(offer) if start != ref.start]
        if not starts:
            return
        response = record(
            result.reschedule_latency,
            result.tally,
            client.post(f"/api/v1/bookings/{ref.booking_id}/reschedule", {"new_start": starts[0]}),
        )
        if response.ok:
            with lock:
                result.rescheduled += 1

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="followup") as pool:
        list(pool.map(follow_up, follow_ups))

    return result


# --------------------------------------------------------------------------------------
# Phase 2 — adversarial concurrency
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RaceOutcome:
    name: str
    contenders: int
    winners: int
    refusals_by_code: dict[str, int]
    unexpected: list[str]
    latency: Latency

    @property
    def refusals(self) -> int:
        return sum(self.refusals_by_code.values())


def _fire_together(calls: list[Callable[[], Response]], name: str) -> RaceOutcome:
    """Release N prepared calls at the same instant and classify what came back."""
    barrier = threading.Barrier(len(calls))
    responses: list[Response] = []
    lock = threading.Lock()
    latency = Latency(name)

    def run(call: Callable[[], Response]) -> None:
        # Every thread blocks here until the last one arrives; then all are released together.
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        response = call()
        latency.record(response.elapsed_ms)
        with lock:
            responses.append(response)

    with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="race") as pool:
        list(pool.map(run, calls))

    winners = sum(1 for r in responses if r.ok)
    refusals: dict[str, int] = {}
    unexpected: list[str] = []
    for response in responses:
        if response.ok:
            continue
        code = response.error_code or f"http_{response.status}"
        refusals[code] = refusals.get(code, 0) + 1
        # A refusal that is not a clean domain conflict is worth reading verbatim: a 500 or a
        # transport error under load is a finding, not a tidy row in a table of expected codes.
        if response.status not in (400, 409, 422):
            unexpected.append(f"{response.status} {response.text[:120]}")
    return RaceOutcome(
        name=name,
        contenders=len(calls),
        winners=winners,
        refusals_by_code=refusals,
        unexpected=unexpected,
        latency=latency,
    )


def race_same_slot(
    stack: StackConfig, business: Business, *, start: str, contenders: int
) -> RaceOutcome:
    """N simultaneous bookings of the IDENTICAL slot. Exactly one may win (RF-04)."""
    event_type = business.event_types["standard"]

    def make(index: int) -> Callable[[], Response]:
        return lambda: book(
            Client(stack.api_url, business.config.api_key),
            event_type_id=event_type.id,
            start=start,
            guest_name=f"Race Contender {index}",
            guest_email=f"race.{index}@guests.sim.test",
            guest_timezone="UTC",
            locale="en",
        )

    return _fire_together([make(index) for index in range(contenders)], "race_same_slot")


def race_distinct_slots(
    stack: StackConfig, business: Business, *, starts: list[str], tag: str
) -> RaceOutcome:
    """==The control for the race oracle.== Same burst, N DIFFERENT slots, so N must win.

    If this returns one winner the harness is broken — its threads are not really concurrent, or it
    is miscounting — and the same-slot result above proves nothing at all.
    """
    event_type = business.event_types["standard"]

    def make(index: int, start: str) -> Callable[[], Response]:
        return lambda: book(
            Client(stack.api_url, business.config.api_key),
            event_type_id=event_type.id,
            start=start,
            guest_name=f"Control Contender {index}",
            guest_email=f"control.{tag}.{index}@guests.sim.test",
            guest_timezone="UTC",
            locale="en",
        )

    calls = [make(index, start) for index, start in enumerate(starts)]
    return _fire_together(calls, "control_race_distinct_slots")


def race_cancel(
    stack: StackConfig, business: Business, *, booking_id: str, contenders: int
) -> RaceOutcome:
    """N simultaneous cancels of ONE booking.

    ==The oracle here is NOT the count of 200s.== ``cancel_booking`` is deliberately idempotent, so
    every contender may legitimately receive one: the loser "sees it already cancelled and is a
    no-op that queues NO second webhook". The invariant that matters is that exactly ONE
    ``booking.cancelled`` webhook is emitted, and that is checked at the sink by
    :func:`sink_events_for_booking` — not here. A harness asserting a single 200 would have failed a
    correct product.
    """

    def make() -> Callable[[], Response]:
        return lambda: Client(stack.api_url, business.config.api_key).post(
            f"/api/v1/bookings/{booking_id}/cancel"
        )

    return _fire_together([make() for _ in range(contenders)], "race_cancel_same_booking")


def race_reschedule(
    stack: StackConfig, business: Business, *, booking_id: str, starts: list[str]
) -> RaceOutcome:
    """N simultaneous reschedules of ONE booking to N DIFFERENT slots. Exactly one may win.

    The partial unique index cannot catch this one — every contender proposes a different
    ``start_at`` — so it is the advisory lock, and only the advisory lock, being tested.
    """

    def make(start: str) -> Callable[[], Response]:
        return lambda: Client(stack.api_url, business.config.api_key).post(
            f"/api/v1/bookings/{booking_id}/reschedule", {"new_start": start}
        )

    return _fire_together([make(start) for start in starts], "race_reschedule_same_booking")


def race_cancel_vs_reschedule(
    stack: StackConfig, business: Business, *, booking_id: str, start: str
) -> RaceOutcome:
    """A cancel and a reschedule of the same booking, released together."""

    def cancel() -> Response:
        return Client(stack.api_url, business.config.api_key).post(
            f"/api/v1/bookings/{booking_id}/cancel"
        )

    def reschedule() -> Response:
        return Client(stack.api_url, business.config.api_key).post(
            f"/api/v1/bookings/{booking_id}/reschedule", {"new_start": start}
        )

    return _fire_together([cancel, reschedule], "race_cancel_vs_reschedule")


# --------------------------------------------------------------------------------------
# The sink — the only place "exactly one webhook" is observable
# --------------------------------------------------------------------------------------


def _captured(sink_url: str) -> list[dict[str, Any]]:
    body: Any = Client(sink_url).get("/_captured").body
    captured: Any = body.get("captured", []) if isinstance(body, dict) else []
    return (
        [entry for entry in captured if isinstance(entry, dict)]
        if isinstance(captured, list)
        else []
    )


def count_sink_events(sink_url: str) -> tuple[dict[str, int], int]:
    """Count captured deliveries by event name. Returns ``(counts, unreadable)``.

    ``unreadable`` is surfaced rather than swallowed: a payload shape this cannot parse would
    otherwise present as "zero duplicate cancellations" — the answer we were hoping for, and the one
    a broken reader always gives.
    """
    counts: dict[str, int] = {}
    unreadable = 0
    for entry in _captured(sink_url):
        try:
            payload: Any = json.loads(base64.b64decode(str(entry.get("body_b64", ""))))
        except (ValueError, TypeError):
            unreadable += 1
            continue
        event: Any = payload.get("event") if isinstance(payload, dict) else None
        if isinstance(event, str):
            counts[event] = counts.get(event, 0) + 1
        else:
            unreadable += 1
    return counts, unreadable


def sink_events_for_booking(sink_url: str, booking_id: str) -> dict[str, int]:
    """Deliveries mentioning ``booking_id``, by event name — the per-booking duplicate check."""
    counts: dict[str, int] = {}
    for entry in _captured(sink_url):
        try:
            raw = base64.b64decode(str(entry.get("body_b64", "")))
        except (ValueError, TypeError):
            continue
        if booking_id.encode() not in raw:
            continue
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            continue
        event: Any = payload.get("event") if isinstance(payload, dict) else None
        if isinstance(event, str):
            counts[event] = counts.get(event, 0) + 1
    return counts


# --------------------------------------------------------------------------------------
# Phase 3 — the controls
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Control:
    """One control: what it guards, what it expected, what it got, and whether it held.

    ==``ran`` is a field rather than a sentinel string in ``observed``.== "Did not run" and "ran and
    failed" are different facts with different consequences — the first makes a run INCOMPLETE, the
    second makes it VOID — and encoding that difference in prose the reporter has to string-match is
    how a skipped control eventually gets counted as a passing one.
    """

    ident: str
    guards: str
    expected: str
    observed: str
    passed: bool
    ran: bool = True

    @classmethod
    def not_run(cls, ident: str, guards: str, expected: str, why: str) -> Control:
        """A control that could not be executed. Never silently omitted from the report."""
        return cls(
            ident=ident,
            guards=guards,
            expected=expected,
            observed=f"NOT RUN — {why}",
            passed=False,
            ran=False,
        )


def control_taken_slot(stack: StackConfig, business: Business, *, start: str) -> Control:
    """A slot that is already taken must be REFUSED — the control for "no double-bookings"."""
    response = book(
        Client(stack.api_url, business.config.api_key),
        event_type_id=business.event_types["standard"].id,
        start=start,
        guest_name="Late Arrival",
        guest_email="late.arrival@guests.sim.test",
        guest_timezone="UTC",
        locale="en",
    )
    return Control(
        ident="C1",
        guards="the no-double-booking claim: an already-taken slot is refused",
        expected="409 slot_unavailable",
        observed=f"{response.status} {response.error_code}",
        passed=response.status == 409 and response.error_code == "slot_unavailable",
    )


def control_closed_day(stack: StackConfig, business: Business, *, saturday: date) -> Control:
    """A business closed at weekends must offer NOTHING on a Saturday."""
    response = fetch_slots(
        Client(stack.api_url, business.config.api_key),
        event_type_id=business.event_types["standard"].id,
        day=saturday,
        timezone=business.config.timezone,
    )
    starts = slot_starts(response)
    return Control(
        ident="C3",
        guards="the schedule is enforced: a closed weekend offers no slots",
        expected="0 slots offered on a Saturday",
        observed=f"{len(starts)} slots offered",
        passed=len(starts) == 0,
    )


def control_day_cap(stack: StackConfig, business: Business, *, day: date) -> Control:
    """``max_per_day=2`` must stop the third booking of the day (RF-14)."""
    client = Client(stack.api_url, business.config.api_key)
    event_type = business.event_types["capped"]
    outcomes: list[str] = []
    for index in range(3):
        offer = fetch_slots(
            client, event_type_id=event_type.id, day=day, timezone=business.config.timezone
        )
        starts = slot_starts(offer)
        if not starts:
            outcomes.append("no_slots_offered")
            continue
        response = book(
            client,
            event_type_id=event_type.id,
            start=starts[0],
            guest_name=f"Cap Probe {index}",
            guest_email=f"cap.probe.{index}@guests.sim.test",
            guest_timezone="UTC",
            locale="en",
        )
        outcomes.append(f"{response.status}/{response.error_code or 'ok'}")
    # The first two must be created; the third must be stopped — either by the cap at create time or
    # by the day leaving the offer, which is the same guarantee enforced one step earlier (the
    # service applies the cap in compute_slots precisely so a capped day stops being offered).
    passed = (
        len(outcomes) == 3
        and outcomes[0].startswith("201")
        and outcomes[1].startswith("201")
        and (outcomes[2] == "no_slots_offered" or "day_full" in outcomes[2])
    )
    return Control(
        ident="C4",
        guards="the daily cap bites (max_per_day=2)",
        expected="two created, the third refused (day_full) or no longer offered",
        observed=" → ".join(outcomes),
        passed=passed,
    )


def control_no_show_before_end(
    stack: StackConfig, business: Business, *, booking_id: str
) -> Control:
    """Marking a no-show on an appointment that has not ended must be refused."""
    response = Client(stack.api_url, business.config.api_key).post(
        f"/api/v1/bookings/{booking_id}/no-show"
    )
    return Control(
        ident="C6",
        guards="the no-show guard: an appointment that has not ended cannot be a no-show",
        expected="409 not_ended",
        observed=f"{response.status} {response.error_code}",
        passed=response.status == 409 and response.error_code == "not_ended",
    )


def control_drain_deadman(  # noqa: PLR0913 - three injected effects plus two timing knobs
    sampler: OutboxSampler,
    stop_worker: Callable[[], None],
    start_worker: Callable[[], None],
    make_work: Callable[[], None],
    *,
    grow_seconds: float = 25.0,
    drain_timeout: float = 240.0,
) -> tuple[Control, dict[str, float]]:
    """==The instrument's own control.== Strand real work; watch the metric see it, then clear it.

    Without this, every backlog number in the report is unfalsifiable: a scraper wired to a constant
    zero draws the same flat, healthy line as a queue that is genuinely keeping up.

    .. rubric:: Why this does NOT read the backlog during the outage

    The obvious version of this control — stop the worker, scrape, watch ``due`` climb — cannot
    work, and finding that out is itself a result worth keeping. ==``/metrics/summary`` is served BY
    THE WORKER== (``api/operator.py`` moved it there deliberately, so the numbers come from the
    process holding the BYPASSRLS pool and are therefore true). One process serves the metric and
    drains the queue, so while the drain is down the metric is not late — it is **absent**:
    ``Connection refused``, not a climbing number.

    So the observable sequence is the one this runs instead:

    1. read the baseline;
    2. stop the worker and create real bookings, whose intents nobody can drain;
    3. confirm the operator surface is genuinely UNREACHABLE (which also proves the stop worked,
       rather than assuming ``docker compose stop`` did what it was asked);
    4. restart, and read the FIRST reading that comes back — it must show the stranded work;
    5. wait for it to return to zero.

    A scraper wired to a constant zero fails step 4. A queue that never recovers fails step 5. What
    this cannot prove — and no scrape of this endpoint ever could — is that a **dead** worker is
    visible in the metric. It is not. That needs external liveness monitoring, and the report says
    so rather than leaving a reader to assume the backlog alarm covers it.
    """
    baseline = sampler.scrape().due
    # The background sampler would otherwise log the deliberate outage as scrape failures, and the
    # report would warn about an instrument that was working exactly as intended.
    sampler.pause()
    stop_worker()
    make_work()
    time.sleep(grow_seconds)

    # Step 3: the outage must be REAL. If the surface still answers, the worker never stopped and
    # everything below would be measuring a drain that never paused.
    surface_down = False
    try:
        sampler.scrape()
    except OutboxScrapeError:
        surface_down = True

    start_worker()

    # Step 4: the first reading the restarted worker gives us. Poll through the boot window, taking
    # the earliest successful scrape — the stranded work must still be visible in it.
    started_waiting = time.monotonic()
    boot_deadline = started_waiting + 90.0
    first_reading: int | None = None
    first_age = 0.0
    while time.monotonic() < boot_deadline:
        try:
            sample = sampler.scrape()
        except OutboxScrapeError:
            time.sleep(0.25)
            continue
        first_reading = sample.due
        first_age = sample.oldest_due_age_seconds
        break

    # Step 5: and it must clear.
    deadline = time.monotonic() + drain_timeout
    drained_after: float | None = None
    while time.monotonic() < deadline:
        try:
            if sampler.scrape().due == 0:
                drained_after = time.monotonic() - started_waiting
                break
        except OutboxScrapeError:
            pass
        time.sleep(1.0)
    sampler.resume()

    passed = surface_down and first_reading is not None and first_reading > baseline
    passed = passed and drained_after is not None
    return (
        Control(
            ident="C5",
            guards="the backlog metric is LIVE (stranded work is seen, and then cleared)",
            expected=(
                "the operator surface goes unreachable with the worker; on restart the first "
                "reading exceeds the baseline, then returns to 0"
            ),
            observed=(
                f"baseline due={baseline}; surface unreachable during the outage: "
                f"{'yes' if surface_down else 'NO'}; first reading after restart="
                f"{first_reading if first_reading is not None else 'none'} "
                f"(oldest {first_age:.1f}s); "
                + (
                    f"drained in {drained_after:.1f}s"
                    if drained_after is not None
                    else "NEVER drained"
                )
            ),
            passed=passed,
        ),
        {
            "baseline_due": float(baseline),
            "surface_unreachable_during_outage": 1.0 if surface_down else 0.0,
            "first_reading_after_restart": float(
                first_reading if first_reading is not None else -1
            ),
            "first_reading_oldest_age_seconds": first_age,
            "drain_recovery_seconds": drained_after if drained_after is not None else -1.0,
        },
    )


def control_single_winner(
    race: RaceOutcome | None,
    *,
    ident: str,
    guards: str,
    expected_refusal: str,
) -> Control:
    """==THE claim of the whole report, finally wired to the verdict.==

    The same-slot race was reported in a table and nowhere else: the run would have stamped
    ``MEASURED`` with five winners on one slot, because nothing compared its result to anything.
    The headline number of the document could not invalidate the document.

    Passing requires all three of:

    * **exactly one winner** — the RF-04 guarantee itself;
    * **every other contender refused with the expected code** (a slot that is taken must answer
      ``slot_unavailable``, not some other conflict that happens to be a 409);
    * **no unexpected responses** — a 5xx or a transport error under load is a finding, and a race
      that produced one winner because the other 39 crashed is not a passing race.
    """
    if race is None:
        return Control.not_run(ident, guards, "exactly 1 winner", "the race never ran")
    codes = set(race.refusals_by_code)
    passed = (
        race.winners == 1
        and race.refusals == race.contenders - 1
        and codes <= {expected_refusal}
        and not race.unexpected
    )
    return Control(
        ident=ident,
        guards=guards,
        expected=f"exactly 1 winner of {race.contenders}, the rest `{expected_refusal}`",
        observed=(
            f"{race.winners} winner(s), {race.refusals} refused {race.refusals_by_code}"
            + (f", UNEXPECTED: {race.unexpected}" if race.unexpected else "")
        ),
        passed=passed,
    )


def control_outbox_drained(
    *, drained: bool, waited_seconds: float, scrape_failures: int
) -> Control:
    """The queue must actually empty, and the instrument must have been readable throughout.

    A run that ends with work still due did not observe the drain keeping up — it observed it
    losing — and the latency figures for booking→confirmation describe only the messages that made
    it out. Scrape failures count too: backlog numbers computed over a series with holes in it
    understate the peak, so an unexplained hole invalidates rather than decorates the result. (The
    deliberate outage in C5 pauses the sampler, so those failures never reach this count.)
    """
    passed = drained and scrape_failures == 0
    return Control(
        ident="C12",
        guards="the outbox actually drained, and the backlog was readable throughout",
        expected="due == 0 before the run ends, with 0 unexplained scrape failures",
        observed=(
            f"drained={drained} after {waited_seconds:.1f}s; "
            f"unexplained scrape failures={scrape_failures}"
        ),
        passed=passed,
    )


def pick_micro_slot(
    starts: list[str], *, duration_seconds: int, budget_seconds: float, now: datetime
) -> str | None:
    """The first offered slot that will really END inside this run's waiting budget.

    ==The no-show leg used to take ``starts[0]`` and then cap its wait with ``min(...)``.== If that
    slot ended later than the cap, the harness stopped waiting, marked the no-show anyway, and
    recorded whatever came back — a `409 not_ended` would have been filed as the observed outcome of
    a *positive* test. Choosing the slot by its end time removes the need for a cap at all: either a
    slot fits the budget and is waited out in full, or none does and the control says NOT RUN.
    """
    for start in starts:
        ends_in = (parse_iso(start) + timedelta(seconds=duration_seconds) - now).total_seconds()
        if 0 <= ends_in <= budget_seconds:
            return start
    return None


def active_bookings_for_guest(
    stack: StackConfig,
    business: Business,
    *,
    guest_email: str,
    date_from: date,
    date_to: date,
) -> list[str]:
    """Every ACTIVE booking belonging to one guest in a window, as ``"<id> <status>"`` strings.

    Pages through the envelope rather than trusting one response: ``GET /bookings/`` hard-caps
    ``limit`` at 500, and a run of this size can exceed that. Reading only the first page would
    return "0 active" for a lineage that exists further down — the most reassuring possible answer,
    arrived at by not looking.
    """
    client = Client(stack.api_url, business.config.api_key)
    query = f"from={date_from.isoformat()}&to={date_to.isoformat()}&limit=500"
    found: list[str] = []
    offset = 0
    while True:
        body: Any = client.get(f"/api/v1/bookings/?{query}&offset={offset}").body
        if not isinstance(body, dict):
            break
        items: Any = body.get("items", [])
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("guest_email", "")).lower() != guest_email.lower():
                continue
            status = str(item.get("status", ""))
            if status in ("pending", "confirmed"):
                found.append(f"{item.get('id')} {status}")
        offset += len(items)
        if offset >= int(body.get("total", 0)):
            break
    return found


def control_lineage_after_race(  # noqa: PLR0913 - identity, subject and window are all required
    stack: StackConfig,
    business: Business,
    *,
    ident: str,
    guards: str,
    guest_email: str,
    date_from: date,
    date_to: date,
    at_most: bool = False,
) -> Control:
    """==What a mutation race left BEHIND, which is the invariant that actually matters.==

    Counting HTTP winners is not enough, and the cancel-vs-reschedule race is the proof: both calls
    can legitimately answer 200 (the reschedule swaps in a successor; the cancel then finds the
    predecessor already cancelled and is an idempotent no-op), so "2 winners" is not a defect and
    "1 winner" is not the contract. The contract is about the diary: ==the guest must not end up
    holding two live appointments.==

    ``at_most`` distinguishes the two shapes. A pure reschedule race must leave EXACTLY one
    successor. A cancel racing a reschedule may legitimately leave one (the reschedule got there
    first) or none (the cancel did) — but never two.
    """
    active = active_bookings_for_guest(
        stack, business, guest_email=guest_email, date_from=date_from, date_to=date_to
    )
    passed = len(active) <= 1 if at_most else len(active) == 1
    return Control(
        ident=ident,
        guards=guards,
        expected=(
            "at most 1 active booking survives" if at_most else "exactly 1 successor survives"
        ),
        observed=f"{len(active)} active: {active or 'none'}",
        passed=passed,
    )


def next_saturday(from_day: date) -> date:
    """The first Saturday on or after ``from_day`` — the closed day the schedule control uses."""
    return from_day + timedelta(days=(5 - from_day.weekday()) % 7)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
