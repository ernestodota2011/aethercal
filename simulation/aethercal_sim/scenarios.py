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
import contextlib
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
from .measure import (
    ErrorTally,
    Latency,
    OutboxSample,
    OutboxSampler,
    OutboxScrapeError,
    record,
    wait_for_drain,
)
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


@dataclass(frozen=True, slots=True)
class OfferRead:
    """The day's offer — and ==whether "no slots" was an ANSWER or a failure to obtain one.==

    :func:`slot_starts` returns ``[]`` for a closed Saturday, for a 500, for a 401, for a refused
    connection, and for a body that is not the slots contract at all. Five different facts wearing
    one shape — and every caller that branched on ``if not starts`` silently chose the most
    flattering reading of them: ==a broken instrument filed as a fully-booked day.==

    ``complete`` separates the two. It is the harness's own question — *would this still look the
    same if the API stopped answering?* — asked of the hottest read in the run, where the answer
    was yes in three places at once.
    """

    starts: list[str]
    complete: bool
    problem: str = ""


#: ==The ONLY refusal ordinary organic concurrency can produce on the reschedule path.==
RESCHEDULE_COLLISION_CODE = "slot_unavailable"


def is_reschedule_collision(response: Response) -> bool:
    """Is this refused reschedule the ordinary crowding race, or a finding?

    ==Extracted so the rule can be tested at all.== It first lived inline in ``run_organic``'s
    follow-up worker, where the only tests that could reach it built an :class:`OrganicResult` by
    hand — so they pinned the JUDGE and left the CLASSIFIER unbound. A mutation that deleted the
    distinction entirely kept every test green, which is the same hollow-test shape this harness
    hunts in the product: *if breaking it changes nothing, nothing was checking it.*

    The line is drawn by MACHINE CODE, never by status class. ``not_active`` is a 409 too and would
    mean the lineage was already broken; a 5xx is a finding; a transport error is a finding. Only
    "somebody took that slot between the read and the move" is traffic — the create leg's
    ``collisions`` one mutation later.
    """
    return not response.ok and response.error_code == RESCHEDULE_COLLISION_CODE


def read_offer(response: Response) -> OfferRead:
    """Judge a slots response BEFORE its emptiness is allowed to mean anything.

    A well-formed answer is a 2xx whose body is an object carrying a ``slots`` LIST. Anything else
    is a read that FAILED, and this names how — so a caller counts it as its own outcome instead of
    as a day with nothing left in it.
    """
    if not response.ok:
        return OfferRead(
            [],
            False,
            f"the slots query FAILED: {response.status} {response.error_code} "
            f"{response.text[:120]!r}",
        )
    body: Any = response.body
    if not isinstance(body, dict) or not isinstance(body.get("slots"), list):
        return OfferRead(
            [], False, f"200 but the body is not the slots contract: {response.text[:140]!r}"
        )
    return OfferRead(slot_starts(response), True)


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
    """A booking the run really created, and the wall-clock instant its POST was SENT.

    ``sent_at_wall`` is :func:`time.time`, not a monotonic counter, because it is subtracted from
    Mailpit's own ``Created`` timestamp to measure drain latency. Both are read on the same host, so
    they share one clock.

    ==It is stamped BEFORE the request goes out, and that is the whole point of the field's name.==
    It used to be stamped after the 201 came back, which made the reference instant later than the
    event it is supposed to precede: the worker can pick the intent up, send the mail and have
    Mailpit stamp it while the guest's own HTTP response is still in flight. Those confirmations
    produced a NEGATIVE delta and were then dropped by a bare ``if delta_ms >= 0``, with a comment
    blaming clocks that were never checked.

    The bias is not random. Only the FASTEST confirmations can precede their own POST, so the
    discard removed exactly the left tail — and every published percentile moved UP. ==A silent
    filter whose selection criterion is "too good" is the worst kind of instrument: it is
    conservative in the direction that never gets questioned.== Referenced to the send instant a
    confirmation cannot legitimately precede its cause, so a negative delta now means one thing
    only — the two clocks really disagree — which is counted, reported, and gates the run (C14).
    """

    booking_id: str
    business_slug: str
    guest_email: str
    start: str
    sent_at_wall: float


# --------------------------------------------------------------------------------------
# Phase 1 — organic two-week load
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class OrganicResult:
    """Every planned intent's fate, in ==one exhaustive taxonomy that must add up.==

    .. rubric:: Why the categories are a closed set, and why the count is reconciled

    This used to record three outcomes (booked, collision, no-slots) and let everything else fall
    off the end of the function. Two ``return`` statements did the falling: a slots query that
    FAILED produced no starts and was counted as ``no_slots_offered`` — *the day was full* — and a
    booking that answered anything other than 201 or ``slot_unavailable`` was discarded by a bare
    ``if not response.ok: return``. ==A 500 storm through the whole organic phase would have shown
    up as a quiet day with a slightly smaller "created" number, and the verdict would still have
    read MEASURED.==

    So every planned item now terminates in exactly one category below, and C13 asserts that the
    categories SUM to the number of items planned. That reconciliation is what makes the taxonomy
    load-bearing rather than decorative: a future edit that adds a fourth silent ``return`` breaks
    the arithmetic instead of shrinking a number nobody audits. ==An outcome that fits no known
    category is a failure of the control, not a silence.==
    """

    booked: list[BookedRef] = field(default_factory=list)
    slots_latency: Latency = field(default_factory=lambda: Latency("slots_read"))
    booking_latency: Latency = field(default_factory=lambda: Latency("booking_create"))
    cancel_latency: Latency = field(default_factory=lambda: Latency("booking_cancel"))
    reschedule_latency: Latency = field(default_factory=lambda: Latency("booking_reschedule"))
    tally: ErrorTally = field(default_factory=ErrorTally)

    # ---- the create leg: one category per planned booking ----
    collisions: int = 0
    """Organic ``slot_unavailable`` refusals — two simulated guests wanting the same time."""
    no_slots_offered: int = 0
    """Attempts that met an empty offer **from a well-formed 2xx** — a genuinely full day."""
    slots_read_failed: list[str] = field(default_factory=list)
    """==Attempts whose slots query BROKE.== Formerly indistinguishable from a full day."""
    booking_refused: dict[str, int] = field(default_factory=dict)
    """Non-2xx bookings that were not a collision, by code. Formerly discarded in silence."""
    booking_unreadable: list[str] = field(default_factory=list)
    """A 2xx that is not the booking contract. Used to raise in the pool and kill the run."""

    # ---- the follow-up leg: one category per follow-up actually attempted ----
    follow_ups_attempted: int = 0
    cancelled: int = 0
    cancel_refused: dict[str, int] = field(default_factory=dict)
    """Any non-2xx cancel. ``cancel_booking`` is IDEMPOTENT, so it has no ordinary refusal — a
    caller that finds the booking already cancelled still receives 200. Every entry here is a
    finding."""
    rescheduled: int = 0
    reschedule_collisions: int = 0
    """==A reschedule refused ``slot_unavailable``: the same ordinary collision as on the create
    leg, and it has to be counted as one.==

    The follow-up reads the day's offer and then posts the move, so another simulated guest can take
    the chosen slot in between — precisely the race that produces ``collisions`` above, one mutation
    later. The first version of C13 lumped this in with ``reschedule_refused`` and voided the run
    over it: ==a control going red while the product behaved perfectly==, which is the same defect
    class as C5 racing its own drain and C7 reporting a timeout as a duplication. The asymmetry was
    the bug — the create leg had always drawn this distinction, and the follow-up leg simply never
    inherited it.

    C13 caught it on its first live run (`6662feb5`). ==The published run `b72197a2` had it too, and
    nothing noticed==: that report shows **32** organic collisions and **35** total
    `409 slot_unavailable`, and the missing three were reschedule refusals falling out of the
    function through ``if response.ok:`` — counted nowhere, reconciled against nothing. This
    category is what makes §6 close against §1 instead of leaving a remainder."""
    reschedule_refused: dict[str, int] = field(default_factory=dict)
    """Any OTHER non-2xx reschedule, by code — ``not_active``, a 5xx, a transport error. All
    findings."""
    reschedule_no_target: int = 0
    """A well-formed offer holding no slot other than the one already held. Not an error."""
    reschedule_slots_read_failed: list[str] = field(default_factory=list)

    def create_outcomes(self) -> dict[str, int]:
        """The create leg's taxonomy, flattened to counts. ==Must sum to the plan's length.=="""
        return {
            "booked": len(self.booked),
            "collisions": self.collisions,
            "no_slots_offered": self.no_slots_offered,
            "slots_read_failed": len(self.slots_read_failed),
            "booking_refused": sum(self.booking_refused.values()),
            "booking_unreadable": len(self.booking_unreadable),
        }

    def follow_up_outcomes(self) -> dict[str, int]:
        """The follow-up leg's taxonomy. ==Must sum to ``follow_ups_attempted``.=="""
        return {
            "cancelled": self.cancelled,
            "cancel_refused": sum(self.cancel_refused.values()),
            "rescheduled": self.rescheduled,
            "reschedule_collisions": self.reschedule_collisions,
            "reschedule_refused": sum(self.reschedule_refused.values()),
            "reschedule_no_target": self.reschedule_no_target,
            "reschedule_slots_read_failed": len(self.reschedule_slots_read_failed),
        }

    def unexpected_organic_failures(self) -> dict[str, int]:
        """Everything in the two taxonomies that is a FINDING rather than ordinary traffic.

        A collision — on EITHER leg — a full day, and a reschedule with nowhere to go are the
        product behaving. A broken read, a 5xx, an unreadable body and any refusal the domain does
        not document for that call are not. Only the second group gates.

        ==The line is drawn by MACHINE CODE, not by status class.== "Any 409 is fine" would be the
        lazy version of this rule and it would blind the control: a reschedule answering
        ``not_active`` is a 409 too, and it would mean the lineage was already broken. The single
        refusal ordinary organic concurrency can produce on the reschedule path is
        ``slot_unavailable``, so that one — and only that one — is traffic.
        """
        return {
            key: value
            for key, value in {
                "slots_read_failed": len(self.slots_read_failed),
                "booking_refused": sum(self.booking_refused.values()),
                "booking_unreadable": len(self.booking_unreadable),
                "cancel_refused": sum(self.cancel_refused.values()),
                "reschedule_refused": sum(self.reschedule_refused.values()),
                "reschedule_slots_read_failed": len(self.reschedule_slots_read_failed),
            }.items()
            if value
        }


def run_organic(  # noqa: PLR0915 - the taxonomy IS the length: every outcome is one branch
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

    ==Every planned item ends in exactly one category of :class:`OrganicResult`, and nothing falls
    off the end of this function.== See that class for what used to fall, and what it cost.
    """
    result = OrganicResult()
    lock = threading.Lock()
    clients = {b.slug: Client(stack.api_url, b.config.api_key) for b in world.businesses}

    def _tick(bucket: dict[str, int], code: str | None, status: int) -> None:
        key = code or f"http_{status}"
        bucket[key] = bucket.get(key, 0) + 1

    def one(item: PlannedBooking) -> None:
        business = world.by_slug(item.business_slug)
        client = clients[item.business_slug]
        event_type = business.event_types[item.event_key]

        offer = read_offer(
            record(
                result.slots_latency,
                result.tally,
                fetch_slots(
                    client, event_type_id=event_type.id, day=item.day, timezone=item.guest_timezone
                ),
            )
        )
        if not offer.complete:
            # ==NOT `no_slots_offered`.== The instrument broke; the day is not full, it is unknown.
            with lock:
                result.slots_read_failed.append(f"{item.business_slug} {item.day}: {offer.problem}")
            return
        if not offer.starts:
            with lock:
                result.no_slots_offered += 1
            return

        # Seeded per booking, not per thread: which slot a guest picks stays deterministic no matter
        # which worker happens to run them or in what order.
        rng = random.Random(seed * 1_000_003 + item.seq)
        start = rng.choice(offer.starts)
        # ==Stamped BEFORE the request leaves.== See BookedRef.sent_at_wall: taken after the 201
        # arrived, this reference instant could postdate the confirmation it is meant to precede,
        # and the resulting negative deltas were dropped — trimming the fastest confirmations out
        # of the published distribution.
        sent_at = time.time()
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
        if response.error_code == "slot_unavailable":
            with lock:
                result.collisions += 1
            return
        if not response.ok:
            with lock:
                _tick(result.booking_refused, response.error_code, response.status)
            return
        body: Any = response.body
        if not isinstance(body, dict) or "id" not in body or "start" not in body:
            # A 201 without the booking contract used to raise KeyError inside the pool and take the
            # whole run down; now it is one legible, gating outcome among the others.
            with lock:
                result.booking_unreadable.append(f"{item.guest_email}: {response.text[:120]!r}")
            return
        with lock:
            result.booked.append(
                BookedRef(
                    booking_id=str(body["id"]),
                    business_slug=item.business_slug,
                    guest_email=item.guest_email,
                    start=str(body["start"]),
                    sent_at_wall=sent_at,
                )
            )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="guest") as pool:
        list(pool.map(one, plan))

    # ---- follow-ups: the cancellations and reschedules the plan called for ----
    by_email = {ref.guest_email: ref for ref in result.booked}
    follow_ups = [
        item for item in plan if item.follow_up != "none" and item.guest_email in by_email
    ]
    result.follow_ups_attempted = len(follow_ups)

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
            with lock:
                if response.ok:
                    result.cancelled += 1
                else:
                    _tick(result.cancel_refused, response.error_code, response.status)
            return

        event_type = business.event_types[item.event_key]
        # ==Recorded, like every other request the organic phase makes.== This read was the one
        # HTTP call in the phase that reached neither instrument, so §2's `slots_read` counted only
        # the create leg and §6 — which calls itself "every outcome, successes included" — was
        # short by one response per reschedule attempted. A taxonomy with a hole in it cannot be
        # reconciled against anything, which is the whole job C13 was given.
        offer = read_offer(
            record(
                result.slots_latency,
                result.tally,
                fetch_slots(
                    client,
                    event_type_id=event_type.id,
                    day=item.day,
                    timezone=item.guest_timezone,
                    days=3,
                ),
            )
        )
        if not offer.complete:
            with lock:
                result.reschedule_slots_read_failed.append(
                    f"{item.business_slug} {item.day}: {offer.problem}"
                )
            return
        starts = [start for start in offer.starts if start != ref.start]
        if not starts:
            with lock:
                result.reschedule_no_target += 1
            return
        response = record(
            result.reschedule_latency,
            result.tally,
            client.post(f"/api/v1/bookings/{ref.booking_id}/reschedule", {"new_start": starts[0]}),
        )
        with lock:
            if response.ok:
                result.rescheduled += 1
            elif is_reschedule_collision(response):
                # ==The same collision the create leg counts, one mutation later.== The offer was
                # read and then the move was posted; another simulated guest can take that slot in
                # between. Ordinary organic crowding, not a finding — and the asymmetry with the
                # create leg was itself the bug that voided run 6662feb5.
                result.reschedule_collisions += 1
            else:
                _tick(result.reschedule_refused, response.error_code, response.status)

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
    intervals: list[tuple[float, float]] = field(default_factory=list)
    """``(started, finished)`` per contender, on one monotonic clock. ==The evidence of overlap.==

    Counting winners cannot establish that anything was simultaneous, and that gap is why these are
    recorded: see :func:`peak_overlap` and C15."""

    @property
    def refusals(self) -> int:
        return sum(self.refusals_by_code.values())

    @property
    def peak_overlap(self) -> int:
        """The largest number of these requests that were in flight at the same instant."""
        return peak_overlap(self.intervals)


def peak_overlap(intervals: list[tuple[float, float]]) -> int:
    """How many intervals were open simultaneously, at the busiest instant. ==Pure and testable.==

    A sweep over the endpoints. ==Ends are counted BEFORE starts at an identical timestamp==, so a
    request that finishes exactly as the next begins is a handover, not an overlap.

    That tie-break is the whole difference between this measuring something and measuring nothing,
    and the first version had it backwards. It counted starts first, reasoning that the generous
    reading "favours the harness" — which is precisely wrong for a control that demands a MINIMUM
    overlap: a strictly serial burst `(0,1), (1,2), (2,3)` then scored 2 and sailed through the very
    gate built to catch serialisation. ==Whichever way a tie is broken must be the way that can
    FAIL==, or the threshold is decorative. A unit test over abutting intervals caught it.
    """
    events: list[tuple[float, int]] = []
    for started, finished in intervals:
        events.append((started, 1))
        events.append((finished, -1))
    events.sort(key=lambda event: (event[0], event[1]))
    best = current = 0
    for _, delta in events:
        current += delta
        best = max(best, current)
    return best


def _fire_together(calls: list[Callable[[], Response]], name: str) -> RaceOutcome:
    """Release N prepared calls at the same instant, classify what came back, and ==time it.==

    The ``(started, finished)`` pair per call is what lets a later control assert the burst really
    overlapped. Without it the barrier is a mechanism nobody observes, and every claim in §4 about
    simultaneity rests on the code being read rather than on the run being measured.
    """
    barrier = threading.Barrier(len(calls))
    responses: list[Response] = []
    intervals: list[tuple[float, float]] = []
    lock = threading.Lock()
    latency = Latency(name)

    def run(call: Callable[[], Response]) -> None:
        # Every thread blocks here until the last one arrives; then all are released together.
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        started = time.perf_counter()
        response = call()
        finished = time.perf_counter()
        latency.record(response.elapsed_ms)
        with lock:
            responses.append(response)
            intervals.append((started, finished))

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
        intervals=intervals,
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


def decode_delivery(body_b64: str) -> bytes:
    """Un-base64 one captured delivery. ==Strict, because the lax form does not fail — it LIES.==

    ``base64.b64decode`` defaults to ``validate=False``, which *discards* characters outside the
    alphabet instead of raising. A corrupted body therefore decodes to plausible-looking garbage:
    it raises nothing, so it is never counted as unreadable, and it does not contain the booking id
    either, so it reads as "a delivery that is simply not ours". ==A corrupted duplicate would
    vanish into that third category==, which is the one C7 does not look at.

    ``validate=True`` makes the corruption an error (``binascii.Error``, a subclass of
    ``ValueError``), so both sink readers file it where it belongs: unreadable, and gating.
    """
    return base64.b64decode(body_b64, validate=True)


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
            payload: Any = json.loads(decode_delivery(str(entry.get("body_b64", ""))))
        except (ValueError, TypeError):
            unreadable += 1
            continue
        event: Any = payload.get("event") if isinstance(payload, dict) else None
        if isinstance(event, str):
            counts[event] = counts.get(event, 0) + 1
        else:
            unreadable += 1
    return counts, unreadable


def identifier_values(payload: Any) -> set[str]:
    """Every value sitting at an IDENTIFIER-shaped key, anywhere in the payload. ==Pure.==

    Walks the whole structure and collects values under keys named ``id`` or ending in ``_id``.
    That is what makes "this delivery is about booking X" a statement about the payload's identity
    fields rather than about its bytes.
    """
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = str(key).lower()
            if (name == "id" or name.endswith("_id")) and isinstance(value, str | int):
                found.add(str(value))
            found |= identifier_values(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= identifier_values(item)
    return found


def sink_events_for_booking(sink_url: str, booking_id: str) -> tuple[dict[str, int], int]:
    """Deliveries by event name for one booking, plus ==how many could not be READ at all.==

    The per-booking duplicate check is C7's entire oracle, and it used to ``continue`` past any
    delivery whose base64 or JSON it could not decode. ==That silently subtracts from the count C7
    then calls "exactly one".== A second `booking.cancelled` arriving with a body this parser
    chokes on is invisible, and the control certifies idempotency it never observed — a broken read
    producing the reassuring answer, which is the defect this whole directory exists to hunt.

    :func:`count_sink_events` had already learned it ("a payload shape this cannot parse would
    otherwise present as zero duplicate cancellations — the answer we were hoping for, and the one
    a broken reader always gives") and surfaced ``unreadable``. This reader, one function below it,
    went on swallowing them. So it returns the same pair, and C7 refuses to certify while any
    delivery in the sink is unreadable.

    An entry that decodes but mentions a different booking is NOT unreadable — it is simply not
    ours, and skipping it is the function's job.
    """
    counts: dict[str, int] = {}
    unreadable = 0
    for entry in _captured(sink_url):
        try:
            raw = decode_delivery(str(entry.get("body_b64", "")))
        except (ValueError, TypeError):
            # The body could not even be un-base64'd, so whether it mentions this booking is
            # unknowable. Counted, never skipped.
            unreadable += 1
            continue
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            unreadable += 1
            continue
        # ==Matched on the payload's IDENTITY fields, not on a substring of its bytes.==
        # This used to be `booking_id.encode() not in raw`, which counts a delivery for this booking
        # whenever the id appears ANYWHERE in it — inside a cancel/reschedule URL, a description, or
        # a reference to the predecessor a successor was rescheduled from. Two different bookings'
        # events could therefore both be attributed to one of them, and C7's "exactly one" is a
        # count. Nobody reported this; it is the same shape as S16 one instrument over: the pairing
        # rested on a circumstantial property of the bytes instead of on an identity.
        if booking_id not in identifier_values(payload):
            continue
        event: Any = payload.get("event") if isinstance(payload, dict) else None
        if isinstance(event, str):
            counts[event] = counts.get(event, 0) + 1
        else:
            unreadable += 1
    return counts, unreadable


def observe_cancel_webhooks(  # noqa: PLR0913 - a deadline per phase; none of them share a meaning
    sink_url: str,
    booking_id: str,
    *,
    sampler: OutboxSampler,
    drain_timeout: float = 240.0,
    appear_timeout_seconds: float = 60.0,
    settle_seconds: float = 10.0,
    poll_seconds: float = 0.5,
) -> CancelWebhookObservation:
    """Observe what the cancel race really delivered, instead of sleeping and hoping.

    ==Drain first, then poll, then settle.== Each phase removes a different way the old
    ``time.sleep(12)`` could lie:

    * **drain** — the outbox is the durable queue every webhook leaves through, so waiting for
      ``due == 0`` means every delivery the race queued has been ATTEMPTED before the sink is asked
      anything. That is what makes the observation deterministic rather than a guess about how fast
      the drain happens to be today;
    * **poll** — the read then waits for the event to appear, up to an explicit deadline, so
      "arrived at 13 s" stops being reported as "never arrived";
    * **settle** — and it keeps watching afterwards, because the defect this control exists to
      catch is a SECOND delivery, which by definition arrives after the first.

    A duplicate that lands during the settling window is counted. One that lands after it is not —
    no finite observation can promise otherwise, and the window is stated in the report rather than
    left for a reader to assume away.
    """
    drained, drain_wait = wait_for_drain(sampler, timeout_seconds=drain_timeout)
    started = time.monotonic()
    appeared_after: float | None = None
    counts, unreadable = sink_events_for_booking(sink_url, booking_id)
    while counts.get("booking.cancelled", 0) == 0 and time.monotonic() - started < (
        appear_timeout_seconds
    ):
        time.sleep(poll_seconds)
        counts, unreadable = sink_events_for_booking(sink_url, booking_id)
    if counts.get("booking.cancelled", 0) > 0:
        appeared_after = time.monotonic() - started
        time.sleep(settle_seconds)
        counts, unreadable = sink_events_for_booking(sink_url, booking_id)
    return CancelWebhookObservation(
        counts=counts,
        drained=drained,
        drain_wait_seconds=drain_wait,
        appeared_after_seconds=appeared_after,
        appear_timeout_seconds=appear_timeout_seconds,
        settle_seconds=settle_seconds,
        unreadable=unreadable,
    )


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


def judge_closed_day(response: Response) -> Control:
    """==A closed Saturday and a dead API both produce "0 slots". Only one of them is a pass.==

    This control used to read ``len(slot_starts(response)) == 0`` and nothing else, so it went green
    on a 500, a 401, a connection refused, or a body that was not the slots contract at all. It was
    not measuring the schedule; it was measuring the *absence of evidence* and calling it evidence
    of absence — the exact failure this harness exists to catch, one level below the verdict.

    So the emptiness is judged only after the response has proved it is a real, well-formed answer:
    2xx, a JSON object, and a ``slots`` list actually present. Anything else is an executed,
    FAILED control that names what came back.

    ==That judgement now lives in :func:`read_offer`, and this reads it.== It used to be inlined
    here, which is why the organic phase — the other place that turns a slots response into a
    decision — went on counting a failed query as "the day was full" long after C3 had stopped.
    A rule enforced at one of its call sites is a rule with a hole in it.
    """
    offer = read_offer(response)
    if not offer.complete:
        return Control(
            ident="C3",
            guards="the schedule is enforced: a closed weekend offers no slots",
            expected="a 2xx slots response with 0 slots",
            observed=(
                f"{offer.problem} — an empty offer could not be distinguished from a broken one"
            ),
            passed=False,
        )
    body: Any = response.body
    availability = body.get("availability") if isinstance(body, dict) else None
    return Control(
        ident="C3",
        guards="the schedule is enforced: a closed weekend offers no slots",
        expected="0 slots offered on a Saturday, from a well-formed 2xx",
        observed=f"{len(offer.starts)} slots offered (availability={availability!r})",
        passed=len(offer.starts) == 0,
    )


def control_closed_day(stack: StackConfig, business: Business, *, saturday: date) -> Control:
    """A business closed at weekends must offer NOTHING on a Saturday."""
    return judge_closed_day(
        fetch_slots(
            Client(stack.api_url, business.config.api_key),
            event_type_id=business.event_types["standard"].id,
            day=saturday,
            timezone=business.config.timezone,
        )
    )


def cap_probe_blocker(offer: OfferRead) -> str | None:
    """Why a cap probe cannot book — or ``None`` when it can. ==Pure, so the rule is testable.==

    C4's third probe passes when the day has left the offer, so "no slots" is the answer this
    control HOPES for. That makes it the one place where a read that merely *looks* empty is most
    dangerous, and the two cases must never collapse into one string:

    * ``slots_query_failed(...)`` — the read broke, and C4 fails naming what broke;
    * ``no_slots_offered`` — a well-formed 2xx really offered nothing, which is the cap biting.

    Extracted because the judgement that distinguishes them was inline, where the only tests that
    could reach it were the pure ones over the OUTCOME STRINGS — so deleting the distinction left
    them green. Same shape as ``is_reschedule_collision``: the classifier needs its own seam, or
    the mutation that removes it changes nothing anybody is watching.
    """
    if not offer.complete:
        return f"slots_query_failed({offer.problem})"
    if not offer.starts:
        return "no_slots_offered"
    return None


def control_day_cap(stack: StackConfig, business: Business, *, day: date) -> Control:
    """``max_per_day=2`` must stop the third booking of the day (RF-14).

    ==The third probe's pass condition is "the day left the offer", so an empty offer is the
    ANSWER this control is hoping for== — which makes it the one control where a broken read is
    most dangerous. It checked ``offer.ok`` and then called :func:`slot_starts` directly, so a 2xx
    whose body was not the slots contract yielded no starts and read as the cap biting.

    ``.ok`` is only half the question. :func:`read_offer` asks both halves, and this is now its
    third call site — after C3 and the organic phase. ==Extracting a rule does not enforce it; the
    call sites do==, and this one was left behind by the extraction that fixed the other two.
    """
    client = Client(stack.api_url, business.config.api_key)
    event_type = business.event_types["capped"]
    outcomes: list[str] = []
    for index in range(3):
        offer = read_offer(
            fetch_slots(
                client, event_type_id=event_type.id, day=day, timezone=business.config.timezone
            )
        )
        blocker = cap_probe_blocker(offer)
        if blocker is not None:
            outcomes.append(blocker)
            continue
        starts = offer.starts
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
    return judge_day_cap(outcomes)


def judge_day_cap(outcomes: list[str]) -> Control:
    """Turn C4's three probes into a verdict. ==Pure, so the broken-read case is testable.==

    The first two must be created; the third must be stopped — either by the cap at create time or
    by the day leaving the offer, which is the same guarantee enforced one step earlier (the
    service applies the cap in ``compute_slots`` precisely so a capped day stops being offered).

    ==Note what that makes "no slots" here: a PASS condition.== It is the only control in the set
    whose hoped-for answer is an empty offer, which is exactly why a read that merely *looked*
    empty had to stop counting. ``slots_query_failed(...)`` matches neither accepted third outcome,
    so it fails and names what broke.
    """
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


@dataclass(frozen=True, slots=True)
class DeadmanObservation:
    """Everything the dead-man sequence saw, separated into what is DURABLE and what is a snapshot.

    The distinction is the whole fix. See :func:`judge_drain_deadman`.
    """

    work_created: int
    surface_unreachable: bool
    baseline_due: int
    baseline_rows: int
    final_rows: int
    delivered_after_restart: int | None
    """``drain.delivered`` of the RESTARTED worker process — zero at boot, monotonic after."""
    drain_recovery_seconds: float | None
    first_reading_due: int | None
    """Corroboration only. ``None`` = the boot window closed before any scrape succeeded."""
    first_reading_oldest_age_seconds: float = 0.0

    @property
    def rows_delta(self) -> int:
        return self.final_rows - self.baseline_rows

    @property
    def caught_in_flight(self) -> bool:
        """Whether the backlog GAUGE was still elevated when the first scrape landed.

        ==Reported, never required.== It is a coin toss against the drain — see the judge.
        """
        return self.first_reading_due is not None and self.first_reading_due > self.baseline_due


def judge_drain_deadman(observation: DeadmanObservation) -> Control:
    """==C5 — and why "the first reading beats the baseline" was a control racing its subject.==

    The claim is that the backlog metric is LIVE: an instrument wired to a constant zero draws the
    same flat, healthy line as a queue that is genuinely keeping up, so every number in §3 rests on
    this. The sequence that produces it is sound — strand real work behind a stopped worker, prove
    the outage was real, restart, watch it clear.

    ==What was wrong was the ORACLE.== It required ``first_reading > baseline``, where
    ``first_reading`` is the first successful scrape after the restart and ``due`` is an
    INSTANTANEOUS gauge. The restarted worker serves that metric and drains the queue, and it may
    finish draining before the first scrape gets an answer — on a fast tick with a small backlog,
    routinely. The control then reads ``due == 0`` and fails a system that behaved perfectly.
    ==A control that can fail by luck is not a control== — worse, its failure mode here is to
    accuse the product of the harness's own timing.

    So the pass conditions are now the DURABLE ones, which cannot be missed by arriving late:

    * **the outage was real** — the operator surface went unreachable with the worker;
    * **the snapshot SAW the stranded work** — the DB-derived row count grew by at least the work
      created, and it stays grown for ever, unlike ``due``. A constant-zero instrument fails here;
    * **the restarted worker really processed it** — ``drain.delivered``, a counter that resets to
      zero at boot and only climbs, is at least the work created. Readable at any later instant;
    * **and it cleared** — ``due`` returned to zero.

    ``caught_in_flight`` (the old condition) is still recorded and reported, because catching the
    gauge mid-climb is genuine extra evidence when it happens. It is simply no longer the thing the
    verdict hangs on, since whether it happens is decided by a race with the system under test.

    .. rubric:: What this still cannot prove

    That a **dead** worker is visible in the metric. It is not: ``/metrics/summary`` is served BY
    the worker, so while the drain is down the metric is not late, it is absent (``Connection
    refused``). That needs external liveness monitoring, and the report says so rather than leaving
    a reader to assume the backlog alarm covers it.
    """
    reasons: list[str] = []
    if observation.work_created <= 0:
        reasons.append("no work was created during the outage, so nothing was ever stranded")
    if not observation.surface_unreachable:
        reasons.append(
            "the operator surface still ANSWERED during the outage — the worker never stopped"
        )
    if observation.rows_delta < observation.work_created:
        reasons.append(
            f"the DB-derived snapshot grew by only {observation.rows_delta} row(s) for "
            f"{observation.work_created} stranded booking(s) — it is not reading the real table"
        )
    if observation.delivered_after_restart is None:
        reasons.append("the payload carried no `drain.delivered` counter — the contract changed")
    elif observation.delivered_after_restart < observation.work_created:
        reasons.append(
            f"the restarted worker reports {observation.delivered_after_restart} delivered for "
            f"{observation.work_created} stranded booking(s)"
        )
    if observation.drain_recovery_seconds is None:
        reasons.append("the backlog NEVER returned to 0")
    delivered_text = (
        "ABSENT"
        if observation.delivered_after_restart is None
        else str(observation.delivered_after_restart)
    )
    first_due_text = (
        "none" if observation.first_reading_due is None else str(observation.first_reading_due)
    )
    return Control(
        ident="C5",
        guards="the backlog metric is LIVE (stranded work is seen, and then cleared)",
        expected=(
            "the operator surface goes unreachable with the worker; the DB-derived snapshot and "
            "the restarted worker's delivered counter both account for the stranded work; the "
            "backlog then returns to 0"
        ),
        observed=(
            f"stranded {observation.work_created} booking(s); surface unreachable during the "
            f"outage: {'yes' if observation.surface_unreachable else 'NO'}; outbox rows "
            f"{observation.baseline_rows}→{observation.final_rows} (+{observation.rows_delta}); "
            f"restarted worker delivered="
            f"{delivered_text}"
            f"; backlog caught in flight: "
            f"{'yes' if observation.caught_in_flight else 'no (drained before the first scrape)'}"
            f" (first reading due="
            f"{first_due_text}"
            f", oldest {observation.first_reading_oldest_age_seconds:.1f}s); "
            + (
                f"drained in {observation.drain_recovery_seconds:.1f}s"
                if observation.drain_recovery_seconds is not None
                else "NEVER drained"
            )
            + (f" — FAILED: {'; '.join(reasons)}" if reasons else "")
        ),
        passed=not reasons,
    )


def control_drain_deadman(  # noqa: PLR0913, PLR0915 - injected effects, plus a way back from each
    sampler: OutboxSampler,
    stop_worker: Callable[[], None],
    start_worker: Callable[[], None],
    make_work: Callable[[], int],
    *,
    grow_seconds: float = 25.0,
    drain_timeout: float = 240.0,
) -> tuple[Control, dict[str, float]]:
    """==The instrument's own control.== Strand real work; prove the metric accounts for it.

    Without this, every backlog number in the report is unfalsifiable: a scraper wired to a constant
    zero draws the same flat, healthy line as a queue that is genuinely keeping up.

    The sequence:

    1. read the baseline — the backlog gauge AND the durable row count;
    2. stop the worker and create real bookings, whose intents nobody can drain. ``make_work``
       returns HOW MANY it created, because the pass conditions are stated against that number
       rather than against a constant written twice;
    3. confirm the operator surface is genuinely UNREACHABLE (which also proves the stop worked,
       rather than assuming ``docker compose stop`` did what it was asked);
    4. restart, and take the earliest scrape that answers — kept as corroboration;
    5. wait for the backlog to clear, then read the durable signals.

    :func:`judge_drain_deadman` owns what those readings mean, and why step 4 is no longer allowed
    to decide the verdict on its own.
    """
    baseline = sampler.scrape()
    # The background sampler would otherwise log the deliberate outage as scrape failures, and the
    # report would warn about an instrument that was working exactly as intended.
    sampler.pause()

    work_created = 0
    surface_down = False
    first_reading: int | None = None
    first_age = 0.0
    drained_after: float | None = None
    final: OutboxSample | None = None
    started_waiting = time.monotonic()
    worker_stopped = False

    # ==This block STOPS A CONTAINER and PAUSES the instrument, so it needs a way back that does
    # not depend on it finishing.== Without the `finally`, anything raising in between — a compose
    # timeout, a booking call blowing up in `make_work`, an unexpected error in a scrape — left the
    # run dead with the worker still down and the sampler still paused. Every later phase would
    # then be measuring a stack the harness itself had broken, and the report would blame the
    # product. A control allowed to damage the run it belongs to is worse than no control.
    try:
        stop_worker()
        worker_stopped = True
        work_created = make_work()
        time.sleep(grow_seconds)

        # Step 3: the outage must be REAL. If the surface still answers, the worker never stopped
        # and everything below would be measuring a drain that never paused.
        try:
            sampler.scrape()
        except OutboxScrapeError:
            surface_down = True

        start_worker()
        worker_stopped = False

        # Step 4: the earliest reading the restarted worker gives us. It MAY still show the backlog
        # and it may not — the drain is racing us, and that race is why it no longer gates.
        started_waiting = time.monotonic()
        boot_deadline = started_waiting + 90.0
        while time.monotonic() < boot_deadline:
            try:
                sample = sampler.scrape()
            except OutboxScrapeError:
                time.sleep(0.25)
                continue
            first_reading = sample.due
            first_age = sample.oldest_due_age_seconds
            break

        # Step 5: it must clear, and then the durable signals are read.
        deadline = time.monotonic() + drain_timeout
        while time.monotonic() < deadline:
            try:
                sample = sampler.scrape()
            except OutboxScrapeError:
                time.sleep(1.0)
                continue
            final = sample
            if sample.due == 0:
                drained_after = time.monotonic() - started_waiting
                break
            time.sleep(1.0)
    finally:
        # Put the worker back if we are the reason it is down, and always un-pause the instrument.
        # Both are best-effort: a failure here must not mask whatever brought us into the `finally`.
        if worker_stopped:
            with contextlib.suppress(Exception):
                start_worker()
        sampler.resume()

    observation = DeadmanObservation(
        work_created=work_created,
        surface_unreachable=surface_down,
        baseline_due=baseline.due,
        baseline_rows=baseline.rows,
        final_rows=final.rows if final is not None else baseline.rows,
        delivered_after_restart=final.delivered if final is not None else None,
        drain_recovery_seconds=drained_after,
        first_reading_due=first_reading,
        first_reading_oldest_age_seconds=first_age,
    )
    return (
        judge_drain_deadman(observation),
        {
            "work_created": float(work_created),
            "baseline_due": float(baseline.due),
            "surface_unreachable_during_outage": 1.0 if surface_down else 0.0,
            "outbox_rows_baseline": float(baseline.rows),
            "outbox_rows_final": float(observation.final_rows),
            "delivered_after_restart": float(
                observation.delivered_after_restart
                if observation.delivered_after_restart is not None
                else -1
            ),
            "backlog_caught_in_flight": 1.0 if observation.caught_in_flight else 0.0,
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


def judge_race_concurrency(races: list[RaceOutcome]) -> Control:
    """==C15 — the bursts really OVERLAPPED, which no winner count can establish.==

    C2 fires the same code path at N distinct slots and demands N winners, and the README used to
    present that as proof the harness "did not only ever really send one". ==It is not.== Booking N
    different slots strictly one after another also yields N winners. C2 proves the ORACLE can
    count past one; it says nothing about whether anything was simultaneous.

    Worse, the same blind spot covers the headline. A harness that had quietly serialised its
    threads would book the same slot N times in sequence and leave *exactly one winner*, because
    the first takes the slot and the rest are refused on their own — so C10 passes too, C2 passes,
    and §4 reports a 40-way adversarial burst that never happened. The `threading.Barrier` is the
    mechanism meant to prevent that, and until now nothing OBSERVED that it worked: the guarantee
    lived in the code being read, which is the same place the isolation guarantee lived before it
    was derived.

    So each barrier-released race now carries ``(started, finished)`` per contender on one
    monotonic clock, and this asserts that at least two of those intervals were open at the same
    instant. ==Two is the falsifiable core of the claim== — a serialised harness peaks at exactly
    one — and the measured peak is reported per race so a reader sees how far past the minimum the
    run actually got, rather than being told a threshold was cleared.

    A single-contender race is excluded: it cannot overlap with anything and `contender_count`
    already refuses one at the CLI.
    """
    contended = [race for race in races if race.contenders > 1]
    peaks = {race.name: race.peak_overlap for race in contended}
    serialised = sorted(name for name, peak in peaks.items() if peak < 2)
    missing = sorted(race.name for race in contended if not race.intervals)
    reasons: list[str] = []
    if not contended:
        reasons.append("no multi-contender race ran, so no simultaneity was exercised at all")
    if missing:
        reasons.append(f"no timing was recorded for {missing} — overlap is unfalsifiable there")
    if serialised:
        reasons.append(
            f"{serialised} peaked at ONE request in flight: the burst was a QUEUE, not a race, "
            "and every 'exactly one winner' it produced would be meaningless"
        )
    return Control(
        ident="C15",
        guards=(
            "the adversarial bursts were really SIMULTANEOUS — the barrier worked, rather than "
            "being trusted"
        ),
        expected="every multi-contender race has >= 2 requests in flight at the same instant",
        observed=f"peak overlap by race: {peaks or 'none'}"
        + (f" — FAILED: {'; '.join(reasons)}" if reasons else ""),
        passed=not reasons,
    )


def judge_organic_accounting(result: OrganicResult, *, planned: int) -> Control:
    """==C13 — every planned intent ended somewhere KNOWN, and nothing broke on the way.==

    The organic phase is the source of §1's volume and §2's latency, and until this existed it was
    the only phase whose failures could not reach the verdict. Two silent ``return`` statements did
    it: a slots query that failed was filed as ``no_slots_offered`` — read by the report as *the day
    was full* — and any non-2xx booking that was not a collision simply vanished. ==A run in which
    the API 500'd on half its requests would have reported a slightly quieter fortnight and stamped
    itself MEASURED.==

    So this asks two questions that a taxonomy alone cannot answer:

    * **does it add up?** The create categories must sum to the number of items PLANNED, and the
      follow-up categories to the number of follow-ups attempted. A count reconciled against a
      number computed elsewhere is the only kind that can notice its own omissions — the same
      lesson ``REQUIRED_CONTROL_IDS`` learned one level up.
    * **is anything in it a finding?** A collision, a full day and a reschedule with nowhere to go
      are the product working. A broken read, a 5xx, an unreadable body and a refused mutation are
      not, and any of them voids the run rather than shrinking a number quietly.
    """
    creates = result.create_outcomes()
    follow_ups = result.follow_up_outcomes()
    create_total = sum(creates.values())
    follow_up_total = sum(follow_ups.values())
    unexpected = result.unexpected_organic_failures()
    reconciled = create_total == planned and follow_up_total == result.follow_ups_attempted
    detail = ""
    if result.slots_read_failed:
        detail += f" · first slots failure: {result.slots_read_failed[0]}"
    if result.booking_unreadable:
        detail += f" · first unreadable body: {result.booking_unreadable[0]}"
    return Control(
        ident="C13",
        guards=(
            "the organic phase is fully explained: every planned intent lands in a known outcome, "
            "and no request failed unexpectedly"
        ),
        expected=(
            f"{planned} planned = the sum of the create outcomes, "
            f"{result.follow_ups_attempted} follow-ups = the sum of the follow-up outcomes, "
            "and 0 unexpected failures"
        ),
        observed=(
            f"creates {create_total}/{planned} `{creates}`; "
            f"follow-ups {follow_up_total}/{result.follow_ups_attempted} `{follow_ups}`; "
            f"unexpected failures: {unexpected or 'none'}{detail}"
        ),
        passed=planned > 0 and reconciled and not unexpected,
    )


@dataclass(frozen=True, slots=True)
class CancelWebhookObservation:
    """What the sink was actually seen to hold for the cancel race, and how it was seen.

    ==Carries the timing, not just the count, because "none yet" and "never" are the same number.==
    """

    counts: dict[str, int]
    drained: bool
    drain_wait_seconds: float
    appeared_after_seconds: float | None
    """``None`` means nothing arrived inside the window — a DELIVERY fact, not a duplication one."""
    appear_timeout_seconds: float
    settle_seconds: float
    unreadable: int = 0
    """Deliveries in the sink this harness could not decode. ==A duplicate hiding in one of these
    is invisible to ``counts``==, so a non-zero value means C7 did not observe what it certifies."""


def judge_cancel_idempotency(
    observation: CancelWebhookObservation, *, ident: str = "C7", guards: str = ""
) -> Control:
    """==C7 — and the three different things a count of ``booking.cancelled`` can mean.==

    This control used to be ``time.sleep(12)`` followed by one read. That sleep was doing two jobs
    it could not do: standing in for an observation, and standing in for a deadline. If a webhook
    arrived at 13 seconds the sink read 0 and C7 failed — ==indistinguishable from the duplication
    it exists to detect, and pointing at the product for a fault of the harness.== A control that
    reports a timeout as a defect lies in both directions at once: it manufactures failures it did
    not see, and it would report a genuine 0-of-2 the same way.

    The observation is now made rather than waited for. The outbox is drained FIRST, so every
    delivery the race queued has been attempted before the sink is asked anything; then the sink is
    polled until the event appears or an explicit timeout elapses; then a settling window runs so a
    duplicate arriving late is still counted. Three distinguishable verdicts come out:

    * ``the outbox never drained`` — the observation was never valid; say so, do not judge on it;
    * ``nothing arrived within N s`` — a delivery/timeout fact, named as one;
    * ``k delivered`` — the real oracle: exactly one passes, two or more is the idempotency defect.
    """
    delivered = observation.counts.get("booking.cancelled", 0)
    window = (
        f"drained in {observation.drain_wait_seconds:.1f}s, then polled up to "
        f"{observation.appear_timeout_seconds:.0f}s + {observation.settle_seconds:.0f}s settling"
    )
    if observation.unreadable:
        # ==An unreadable delivery could BE the duplicate.== Certifying "exactly one" over a set
        # this harness could not fully read is the reassuring answer a broken reader always gives.
        verdict = (
            f"{observation.unreadable} delivery(ies) in the sink could NOT be decoded, so the "
            f"count of {delivered} is a lower bound — a duplicate hiding in one of them would be "
            "invisible. Refusing to certify idempotency over a set that could not be read"
        )
        passed = False
    elif not observation.drained:
        verdict = (
            f"the outbox did NOT drain within {observation.drain_wait_seconds:.1f}s, so the sink "
            "was read before every queued delivery had been attempted — this run did not observe "
            "idempotency either way"
        )
        passed = False
    elif delivered == 0:
        verdict = (
            f"NOTHING arrived: 0 booking.cancelled within "
            f"{observation.appear_timeout_seconds:.0f}s "
            "of a drained outbox. ==This is a DELIVERY failure, not a duplication one== — the "
            "distinction this control exists to keep"
        )
        passed = False
    else:
        verdict = (
            f"{delivered} booking.cancelled delivered"
            if delivered != 1
            else "exactly 1 booking.cancelled delivered"
        )
        passed = delivered == 1
    return Control(
        ident=ident,
        guards=guards or "an idempotent cancel emits exactly ONE booking.cancelled webhook",
        expected="exactly 1 booking.cancelled at the sink for this booking",
        observed=(
            f"{verdict} · all events {observation.counts} · unreadable deliveries "
            f"{observation.unreadable} · {window}"
        ),
        passed=passed,
    )


@dataclass(frozen=True, slots=True)
class ConfirmationCoverage:
    """Whether the drain-latency distribution describes the whole run or an unstated subset."""

    created: int
    matched: int
    negative_deltas: int
    worst_negative_ms: float
    read_complete: bool
    read_problem: str
    reported_total: int
    page_size: int
    attempts: int
    waited_seconds: float
    duplicate_confirmations: int = 0
    """Bookings whose recipient received MORE than one confirmation — a broken one-to-one."""
    colliding_uids: int = 0
    """Two bookings matched to confirmations carrying the SAME calendar uid: one announcement was
    counted for two bookings, which is the failure a per-recipient match cannot even see."""
    messages_with_invite: int = 0
    superseded: int = 0
    """Bookings with no confirmation whose guest received a cancellation or reschedule instead.

    ==Accounted, not lost.== The product retires a still-queued confirmation when the booking is
    cancelled or rescheduled before the outbox sends it (the row goes to ``voided``), so demanding a
    confirmation for every booking fails a CORRECT run — verified against a live one that carried
    40 voided outbox rows and 25 such guests."""
    unaccounted: int = 0
    """Bookings whose guest received NOTHING at all. A real loss, and the one that gates."""


def judge_confirmation_coverage(coverage: ConfirmationCoverage) -> Control:
    """==C14 — the booking→confirmation figures are COMPLETE, and nothing was quietly dropped.==

    Two silent subtractions used to sit between the mailbox and §2, and both moved the published
    numbers in the flattering direction:

    * the mailbox was read with a hardcoded ``limit=20000``, equal to the ``MP_MAX_MESSAGES`` the
      overlay sets and compared against nothing. Past it the list simply stopped, and a latency
      sample that loses members reports a faster product;
    * confirmations whose timestamp preceded the booking's reference instant were discarded by a
      bare ``if delta_ms >= 0``. Only the FASTEST can do that, so the discard trimmed the left tail
      and every percentile moved up.

    Neither reached the verdict. The report even warned, in §7, that drain latency had *n* samples
    for *m* bookings — a warning is not a control, and a run that shipped with it still said
    MEASURED. So the reconciliation is now a control: the read must be provably whole (paged to
    Mailpit's own total), every created booking must be matched to a message, and a negative delta
    — which cannot happen legitimately once the reference is the SEND instant — is real clock skew,
    counted and gating rather than silently filtered.

    .. rubric:: And a third subtraction, which was the subtlest of them

    "Matched" meant *the earliest message to that recipient*. Every guest also receives their
    cancellation or reschedule notice, so that rule worked only because confirmations normally
    arrive first — ==a property of the order of events, not a check.== In the one case that matters
    — a booking whose confirmation was never sent and whose cancellation was — the earliest and only
    message is the cancellation: the booking counted as confirmed, this control certified a COMPLETE
    sample over a hole in it, and §2 published the delay of a message sent minutes later for another
    reason. ==A delivery failure would have been published as a high latency.==

    A confirmation is now identified by its iTIP identity in the message's own ``.ics``, the
    pairing must be one-to-one, and a calendar uid may be claimed by only one booking.
    """
    reasons: list[str] = []
    if not coverage.read_complete:
        reasons.append(f"the mailbox read was INCOMPLETE ({coverage.read_problem})")
    if coverage.created <= 0:
        reasons.append("no organic booking was created, so the distribution describes nothing")
    if coverage.unaccounted:
        reasons.append(
            f"{coverage.unaccounted} created booking(s) never matched a confirmation and their "
            "guest received NOTHING — those messages are simply missing"
        )
    if coverage.negative_deltas:
        reasons.append(
            f"{coverage.negative_deltas} confirmation(s) preceded the POST that caused them "
            f"(worst {coverage.worst_negative_ms:.1f}ms) — the two clocks disagree"
        )
    if coverage.duplicate_confirmations:
        reasons.append(
            f"{coverage.duplicate_confirmations} booking(s) had MORE than one confirmation to the "
            "same recipient, so the pairing is not one-to-one"
        )
    if coverage.colliding_uids:
        reasons.append(
            f"{coverage.colliding_uids} booking(s) matched a confirmation whose calendar uid was "
            "already claimed — one announcement counted for two bookings"
        )
    return Control(
        ident="C14",
        guards=(
            "the booking→confirmation sample is complete: the mailbox was read whole and every "
            "created booking is in it"
        ),
        expected=(
            f"a complete mailbox read and {coverage.created} of {coverage.created} bookings "
            "matched, with 0 negative deltas"
        ),
        observed=(
            f"matched {coverage.matched}/{coverage.created} (superseded {coverage.superseded}, "
            f"unaccounted {coverage.unaccounted}) after {coverage.attempts} read(s) in "
            f"{coverage.waited_seconds:.1f}s; mailbox read complete={coverage.read_complete} "
            f"(reported total {coverage.reported_total}, page size {coverage.page_size}); "
            f"negative deltas={coverage.negative_deltas}; duplicate confirmations="
            f"{coverage.duplicate_confirmations}; uid collisions={coverage.colliding_uids}; "
            f"messages carrying a calendar invite={coverage.messages_with_invite}"
            + (f" — {'; '.join(reasons)}" if reasons else "")
        ),
        passed=not reasons,
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


@dataclass(frozen=True, slots=True)
class ActiveBooking:
    """One live appointment in a guest's diary. ==Id and status in ONE object, never two lists.==

    The id used to be embedded in a display string (``f"{id} {status}"``) and the control that needs
    it would have had to parse it back out. Two representations of the same fact drift; one of them
    is then the one nobody updates.
    """

    booking_id: str
    status: str

    def __str__(self) -> str:
        return f"{self.booking_id} {self.status}"


@dataclass(frozen=True, slots=True)
class DiaryRead:
    """The result of reading a guest's live appointments — ==and whether the read is TRUSTWORTHY.==

    ``active`` alone is a trap. An unreadable diary yields an empty list, which looks exactly like
    "this guest holds no live appointment" — the reassuring answer, produced by failing to look. C9
    asks "at most one?", and an empty list satisfies that trivially, so a 500 from the bookings
    endpoint would have *passed* the control guarding against double-booked guests.

    So the read reports its own health and the controls refuse to judge cardinality without it.
    """

    active: list[ActiveBooking]
    complete: bool
    problem: str = ""

    @property
    def ids(self) -> list[str]:
        return [booking.booking_id for booking in self.active]


def active_bookings_for_guest(  # noqa: PLR0911 - each return names a DISTINCT way the read broke
    stack: StackConfig,
    business: Business,
    *,
    guest_email: str,
    date_from: date,
    date_to: date,
) -> DiaryRead:
    """Every ACTIVE booking of one guest in a window, plus whether the read can be trusted.

    Pages through the envelope rather than trusting one response: ``GET /bookings/`` hard-caps
    ``limit`` at 500, and a run of this size exceeds that. Reading only the first page would report
    "0 active" for a lineage further down — again, the most reassuring possible answer, arrived at
    by not looking.

    ``complete`` is False on any non-2xx, any body that is not the ``Page`` contract, a non-integer
    ``total``, or pagination that fails to advance (which would otherwise spin, or silently
    truncate the diary).
    """
    client = Client(stack.api_url, business.config.api_key)
    query = f"from={date_from.isoformat()}&to={date_to.isoformat()}&limit=500"
    found: list[ActiveBooking] = []
    offset = 0
    guard = 0
    while True:
        guard += 1
        if guard > 50:
            return DiaryRead(found, False, "pagination did not terminate after 50 pages")
        response = client.get(f"/api/v1/bookings/?{query}&offset={offset}")
        if not response.ok:
            return DiaryRead(
                found,
                False,
                f"GET /bookings/ answered {response.status} {response.error_code} "
                f"{response.text[:100]!r}",
            )
        body: Any = response.body
        if not isinstance(body, dict):
            return DiaryRead(found, False, f"body is not an object: {response.text[:100]!r}")
        items: Any = body.get("items")
        total: Any = body.get("total")
        if not isinstance(items, list) or not isinstance(total, int):
            return DiaryRead(
                found, False, f"body is not the Page contract: {response.text[:120]!r}"
            )
        for item in items:
            if not isinstance(item, dict):
                return DiaryRead(found, False, "a page item is not an object")
            if str(item.get("guest_email", "")).lower() != guest_email.lower():
                continue
            status = str(item.get("status", ""))
            if status in ("pending", "confirmed"):
                found.append(ActiveBooking(str(item.get("id")), status))
        if not items:
            # An empty page before reaching `total` means the server and the envelope disagree;
            # trusting it would silently truncate the diary.
            return (
                DiaryRead(found, True)
                if offset >= total
                else DiaryRead(found, False, f"empty page at offset {offset} of total {total}")
            )
        offset += len(items)
        if offset >= total:
            return DiaryRead(found, True)


def control_lineage_after_race(  # noqa: PLR0913 - identity, subject and window are all required
    stack: StackConfig,
    business: Business,
    *,
    ident: str,
    guards: str,
    guest_email: str,
    date_from: date,
    date_to: date,
    race: RaceOutcome | None,
    original_id: str,
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
    return judge_lineage(
        active_bookings_for_guest(
            stack, business, guest_email=guest_email, date_from=date_from, date_to=date_to
        ),
        ident=ident,
        guards=guards,
        at_most=at_most,
        race=race,
        original_id=original_id,
    )


def judge_lineage(  # noqa: PLR0913 - the verdict needs the read, the race AND the subject
    read: DiaryRead,
    *,
    ident: str,
    guards: str,
    at_most: bool,
    race: RaceOutcome | None,
    original_id: str,
) -> Control:
    """Turn a diary read into a verdict. ==Pure, so both broken-source cases are testable.==

    .. rubric:: Two ways this could pass without proving anything, and both are closed

    **A partial read.** An unreadable diary returns an empty list, and "at most one" is trivially
    true of nothing — so a failing bookings endpoint would have PASSED the very control guarding
    against a guest holding two live appointments. Cardinality is judged only on a COMPLETE read.

    **==A system that never moved.==** This is the one that survived longer. If every request in the
    mutation race failed, the ORIGINAL booking is still sitting there — and a diary holding exactly
    one live appointment is precisely what "one successor survives" looks like. C8 passed. C9's
    "at most one" passed even more easily. ==The control measured a final state compatible with two
    different histories and only one of them is the history it claims to test== — the same defect as
    C2 being read as proof of simultaneity, one control over.

    So the race itself is now evidence, and the survivor's identity is the discriminator:

    * **the race must have produced a winner.** A pure reschedule race has exactly one by contract;
      a cancel racing a reschedule has at least one, because both calls may legitimately answer 200.
      Zero winners means nothing happened, whatever the diary looks like afterwards;
    * ==**whoever survives must not BE the subject.**== A reschedule swaps in a successor, so the
      original must be gone. If the survivor is the original, no mutation took effect — and that is
      exactly the state a fully-failed race leaves behind.

    ``at_most`` still distinguishes the two shapes: a pure reschedule race must leave EXACTLY one
    successor; a cancel racing a reschedule may leave one (the reschedule got there first) or none
    (the cancel did) — but never two, and never the original.
    """
    expected = (
        "at least 1 winner, and at most 1 active booking — never the original"
        if at_most
        else "exactly 1 winner, and exactly 1 SUCCESSOR (not the original) survives"
    )
    if not read.complete:
        return Control(
            ident=ident,
            guards=guards,
            expected=expected,
            observed=(
                f"the diary could not be read completely ({read.problem}) — refusing to judge "
                "cardinality on a partial read"
            ),
            passed=False,
        )

    active = read.active
    reasons: list[str] = []
    if race is None:
        reasons.append("the race outcome was not available, so no mutation could be evidenced")
    elif at_most and race.winners < 1:
        reasons.append(
            f"NOTHING mutated: {race.winners} of {race.contenders} calls succeeded, so the diary "
            "below is the state the run started in"
        )
    elif not at_most and race.winners != 1:
        reasons.append(
            f"the reschedule race left {race.winners} winner(s) of {race.contenders}, not exactly "
            "1 — with 0 nothing mutated at all"
        )
    if at_most and len(active) > 1:
        # The invariant that matters most: a guest must never end up holding two live appointments.
        reasons.append(f"{len(active)} live appointments survive — the guest is double-booked")
    if not at_most and len(active) != 1:
        reasons.append(f"{len(active)} successors survive, expected exactly 1")
    survivors = read.ids
    if original_id in survivors:
        reasons.append(
            f"the survivor IS the original subject ({original_id}) — no mutation took effect, "
            "which is exactly the diary a fully-failed race leaves behind"
        )
    winners_text = "unknown" if race is None else f"{race.winners}/{race.contenders}"
    return Control(
        ident=ident,
        guards=guards,
        expected=expected,
        observed=(
            f"race winners {winners_text}; {len(active)} active: "
            f"{[str(booking) for booking in active] or 'none'}; subject {original_id}"
            + (f" — FAILED: {'; '.join(reasons)}" if reasons else "")
        ),
        passed=not reasons,
    )


def next_saturday(from_day: date) -> date:
    """The first Saturday on or after ``from_day`` — the closed day the schedule control uses."""
    return from_day + timedelta(days=(5 - from_day.weekday()) % 7)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
