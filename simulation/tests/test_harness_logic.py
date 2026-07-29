"""The harness's own unit tests: the pure logic every reported number is computed with.

==A measuring instrument needs its own calibration.== The controls inside a run prove the harness
can see the product misbehave; these prove the arithmetic underneath the report is right, offline
and in milliseconds. A p95 computed wrongly is not a smaller problem than a race counted wrongly —
it is the same problem one layer down, and it would be invisible in a run whose every control
passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from aethercal_sim.__main__ import (
    confirmations_by_recipient,
    contender_count,
    measure_confirmations,
    parse_args,
    positive_int,
)
from aethercal_sim.client import Response, extract_error_code
from aethercal_sim.measure import (
    EXPECTED_OUTBOX_STATUSES,
    CalendarInvite,
    ErrorTally,
    Latency,
    Mailbox,
    MailboxRead,
    MailMessage,
    OutboxSample,
    OutboxSampler,
    OutboxScrapeError,
    parse_summary,
    percentile,
)
from aethercal_sim.report import REQUIRED_CONTROL_IDS, missing_controls, verdict_for
from aethercal_sim.scenarios import (
    ActiveBooking,
    BookedRef,
    CancelWebhookObservation,
    ConfirmationCoverage,
    Control,
    DeadmanObservation,
    DiaryRead,
    OfferRead,
    OrganicResult,
    RaceOutcome,
    _fire_together,
    cap_probe_blocker,
    control_outbox_drained,
    decode_delivery,
    identifier_values,
    is_reschedule_collision,
    judge_cancel_idempotency,
    judge_closed_day,
    judge_confirmation_coverage,
    judge_day_cap,
    judge_drain_deadman,
    judge_lineage,
    judge_organic_accounting,
    judge_race_concurrency,
    next_saturday,
    peak_overlap,
    pick_micro_slot,
    read_offer,
    sink_events_for_booking,
)
from aethercal_sim.traffic import WEEKDAY_WEIGHTS, PlannedBooking, plan_two_weeks, summarise_plan
from aethercal_sim.world import (
    EXPECTED_COMPOSE_PROJECT,
    BusinessConfig,
    NotADisposableStackError,
    StackConfig,
    assert_disposable_stack,
)

BUSINESSES = ["clinica-sonrisa", "katy-hvac", "estudio-legal"]
MIX = {"clinica-sonrisa": 0.85, "katy-hvac": 0.15, "estudio-legal": 0.5}
MONDAY = date(2026, 8, 3)


def _plan(seed: int = 7, per_week: int = 20) -> list[PlannedBooking]:
    return plan_two_weeks(
        business_slugs=BUSINESSES,
        locale_mix=MIX,
        start=MONDAY,
        seed=seed,
        bookings_per_business_per_week=per_week,
    )


# --------------------------------------------------------------------------------------
# Percentiles
# --------------------------------------------------------------------------------------


def test_percentile_is_nearest_rank_and_returns_a_real_observation() -> None:
    """Every quoted percentile must be a latency that was actually measured."""
    samples = [float(n) for n in range(1, 101)]  # 1..100, already sorted
    assert percentile(samples, 0.50) == 50.0
    assert percentile(samples, 0.95) == 95.0
    assert percentile(samples, 0.99) == 99.0
    assert percentile(samples, 1.0) == 100.0


def test_percentile_of_an_empty_sample_is_none_not_zero() -> None:
    """==Zero is a latency; "no data" is not.== Reporting 0.0 would read as instantaneous."""
    assert percentile([], 0.95) is None


@pytest.mark.parametrize("size", list(range(1, 12)))
def test_percentile_never_indexes_past_the_end(size: int) -> None:
    """Small samples are where an off-by-one in nearest rank actually bites."""
    samples = [float(n) for n in range(size)]
    for fraction in (0.5, 0.95, 0.99, 1.0):
        result = percentile(samples, fraction)
        assert result in samples


def test_latency_summary_reports_count_and_extremes() -> None:
    latency = Latency("probe")
    for value in (5.0, 1.0, 9.0):
        latency.record(value)
    summary = latency.summary()
    assert summary["count"] == 3
    assert summary["min"] == 1.0
    assert summary["max"] == 9.0


# --------------------------------------------------------------------------------------
# The error taxonomy
# --------------------------------------------------------------------------------------


def test_error_tally_counts_successes_too() -> None:
    """ "40 failures" means nothing without "out of how many"."""
    conflict = {"detail": {"error": "slot_unavailable"}}
    tally = ErrorTally()
    tally.record(Response(201, {"id": "x"}, "", 1.0, None))
    tally.record(Response(409, conflict, "", 1.0, "slot_unavailable"))
    tally.record(Response(409, conflict, "", 1.0, "slot_unavailable"))
    assert tally.total() == 3
    assert tally.failures() == 2
    assert tally.counts[(409, "slot_unavailable")] == 2


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"detail": {"error": "slot_unavailable", "message": "x"}}, "slot_unavailable"),
        ({"error": "day_full"}, "day_full"),
        ({"detail": [{"loc": ["body"], "msg": "field required"}]}, "validation_error"),
        ({"detail": "plain string"}, None),
        (None, None),
        ("not a dict", None),
    ],
)
def test_extract_error_code_handles_every_envelope_shape(
    body: object, expected: str | None
) -> None:
    """A 422's list-shaped ``detail`` must not be silently filed as "no code"."""
    assert extract_error_code(body) == expected


# --------------------------------------------------------------------------------------
# The traffic plan
# --------------------------------------------------------------------------------------


def test_plan_is_deterministic_for_a_seed() -> None:
    """Same seed, same load — or one run cannot be compared with the next."""
    assert _plan(seed=99) == _plan(seed=99)


def test_a_different_seed_gives_a_different_plan() -> None:
    """A "deterministic" planner that ignored its seed would also pass the test above."""
    assert _plan(seed=1) != _plan(seed=2)


def test_the_plan_never_lands_on_a_weekend() -> None:
    """==The organic schedule is Mon-Fri, so a weekend entry could only be a planner bug.==

    This is the control for §1 of the report: if an executed run ever books on a Saturday, that
    booking did not come from here.
    """
    for item in _plan(seed=3, per_week=30):
        assert item.day.weekday() in WEEKDAY_WEIGHTS
        assert item.day.weekday() < 5


def test_the_plan_spans_two_working_weeks() -> None:
    days = {item.day for item in _plan(seed=3, per_week=30)}
    assert len(days) == 10  # ten weekdays inside a fourteen-day window
    assert min(days) == MONDAY
    assert (max(days) - MONDAY).days <= 13


def test_volume_lands_near_the_requested_target() -> None:
    """Stochastic rounding must not systematically lose the fractional part of each day."""
    per_week = 40
    plan = _plan(seed=11, per_week=per_week)
    expected = per_week * 2 * len(BUSINESSES)
    assert abs(len(plan) - expected) < expected * 0.12


def test_locale_mix_is_respected() -> None:
    plan = _plan(seed=5, per_week=60)
    clinic = [item for item in plan if item.business_slug == "clinica-sonrisa"]
    spanish = [item for item in clinic if item.locale == "es"]
    assert 0.7 < len(spanish) / len(clinic) < 0.98


def test_guest_emails_are_unique_and_confined_to_a_test_domain() -> None:
    """Two guests sharing an address would silently collapse the drain-latency match."""
    emails = [item.guest_email for item in _plan(per_week=40)]
    assert len(emails) == len(set(emails))
    assert all(email.endswith("@guests.sim.test") for email in emails)


def test_summarise_plan_totals_agree_with_the_plan() -> None:
    plan = _plan(per_week=25)
    summary = summarise_plan(plan)
    assert summary["total"] == len(plan)
    assert sum(summary["by_business"].values()) == len(plan)
    assert sum(summary["by_locale"].values()) == len(plan)
    assert "5" not in summary["by_weekday"]  # Saturday
    assert "6" not in summary["by_weekday"]  # Sunday


# --------------------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------------------


def _control(ident: str, *, passed: bool, ran: bool = True) -> Control:
    return Control(ident, "guards", "expected", "observed", passed=passed, ran=ran)


def _full_set(**overrides: Control) -> list[Control]:
    """Every REQUIRED control, all passing, minus whatever a test wants to change."""
    controls = {ident: _control(ident, passed=True) for ident in REQUIRED_CONTROL_IDS}
    controls.update(overrides)
    return list(controls.values())


def test_all_controls_holding_is_measured() -> None:
    assert verdict_for(_full_set()) == "MEASURED"


def test_a_failed_control_voids_the_run() -> None:
    """==A run with a dead control is not a good run with an asterisk; it is no run at all.=="""
    assert verdict_for(_full_set(C2=_control("C2", passed=False))) == "VOID"


def test_a_control_that_did_not_run_makes_the_run_incomplete() -> None:
    assert verdict_for(_full_set(C5=_control("C5", passed=False, ran=False))) == "INCOMPLETE"


def test_a_failure_outranks_a_skip() -> None:
    """The worst TRUE statement wins: a real failure is not softened by an unrelated skip."""
    controls = _full_set(
        C1=_control("C1", passed=False),
        C5=_control("C5", passed=False, ran=False),
    )
    assert verdict_for(controls) == "VOID"


# --------------------------------------------------------------------------------------
# ==The verdict must notice a control that is ABSENT, not merely one that failed.==
#
# Every control used to be appended inside an `if`, so a missing prerequisite produced NO control
# rather than a failing one — and "7 of 7 held" reads exactly as well as "9 of 9 held". A pass count
# derived from the list it summarises cannot see its own omissions, so the required set is named
# independently and absence is checked against it.
# --------------------------------------------------------------------------------------


def test_an_omitted_control_voids_the_run() -> None:
    """A SHORT list of all-passing controls must not read as a clean run."""
    controls = [control for control in _full_set() if control.ident != "C10"]
    assert all(control.passed for control in controls)  # everything present passed
    assert missing_controls(controls) == ["C10"]
    assert verdict_for(controls) == "VOID"


def test_the_empty_control_list_is_void_not_measured() -> None:
    """==The degenerate case: measuring nothing must never be the best possible outcome.=="""
    assert verdict_for([]) == "VOID"


def test_absence_outranks_a_mere_skip() -> None:
    """A control the harness FAILED to report is worse than one it honestly skipped."""
    controls = [
        control
        for control in _full_set(C5=_control("C5", passed=False, ran=False))
        if control.ident != "C11"
    ]
    assert verdict_for(controls) == "VOID"


def test_missing_controls_is_empty_for_a_complete_run() -> None:
    assert missing_controls(_full_set()) == []


def test_the_required_ids_are_the_contiguous_set_the_report_documents() -> None:
    """==The required set and what ``main()`` can emit must not drift apart.==

    If an id is added here and never emitted, every run VOIDs with a confusing "absent" message.
    Asserting the ids are contiguous ``C1..CN`` makes a typo like ``C13`` fail here rather than in
    production.
    """
    assert {f"C{n}" for n in range(1, len(REQUIRED_CONTROL_IDS) + 1)} == REQUIRED_CONTROL_IDS


def test_not_run_control_is_marked_as_such() -> None:
    control = Control.not_run("C5", "guards", "expected", "no compose command")
    assert control.ran is False
    assert control.passed is False
    assert "NOT RUN" in control.observed


# --------------------------------------------------------------------------------------
# Small date helpers
# --------------------------------------------------------------------------------------


def test_next_saturday_from_a_monday_is_that_weeks_saturday() -> None:
    assert next_saturday(MONDAY) == date(2026, 8, 8)
    assert next_saturday(MONDAY).weekday() == 5


def test_next_saturday_of_a_saturday_is_itself() -> None:
    saturday = date(2026, 8, 8)
    assert next_saturday(saturday) == saturday


# --------------------------------------------------------------------------------------
# ==Isolation, enforced rather than promised.==
#
# The earlier runs went to a throwaway container because the operator was told to point them there,
# not because anything stopped them going elsewhere. These assert the refusal happens on the cheap
# local facts, BEFORE any request or write.
# --------------------------------------------------------------------------------------


def _stack(**overrides: object) -> StackConfig:
    base: dict[str, Any] = {
        "api_url": "http://localhost:8000",
        "worker_url": "http://127.0.0.1:8001",
        "booking_url": "http://localhost:5001",
        "mailpit_url": "http://localhost:8025",
        "sink_url": "http://localhost:9099",
        "sink_webhook_url": "http://hooks:9099/hook",
        "metrics_token": "t" * 40,
        "compose_project": EXPECTED_COMPOSE_PROJECT,
        "nonce": "a1b2c3d4" * 4,
        "businesses": [BusinessConfig("b", "B", "UTC", "tid", "uid", "key")],
    }
    base.update(overrides)
    return StackConfig(**base)  # pyright: ignore[reportArgumentType]


def test_a_foreign_compose_project_is_refused() -> None:
    """The shipping project name is the one `down -v` must never be able to reach."""
    with pytest.raises(NotADisposableStackError, match="compose project"):
        assert_disposable_stack(_stack(compose_project="aethercal"))


@pytest.mark.parametrize(
    "field", ["api_url", "worker_url", "booking_url", "mailpit_url", "sink_url"]
)
def test_a_non_loopback_endpoint_is_refused(field: str) -> None:
    """A LAN address is, by definition, an instance somebody else is using."""
    with pytest.raises(NotADisposableStackError, match="not loopback"):
        assert_disposable_stack(_stack(**{field: "http://192.168.0.250:8000"}))


def test_a_public_hostname_is_refused() -> None:
    """==The concrete case this exists to make impossible.=="""
    with pytest.raises(NotADisposableStackError, match="not loopback"):
        assert_disposable_stack(_stack(api_url="https://book.aetherlogik.com"))


@pytest.mark.parametrize("nonce", ["", "short", "z" * 32, "A1B2" * 8])
def test_a_missing_or_malformed_nonce_is_refused(nonce: str) -> None:
    with pytest.raises(NotADisposableStackError, match="nonce"):
        assert_disposable_stack(_stack(nonce=nonce))


# --------------------------------------------------------------------------------------
# ==Controls must FAIL on a broken source, never pass.==
#
# C3 read `len(starts) == 0` and nothing else, so a 500, a 401 or a refused connection all produced
# its pass condition: it measured absence of evidence and called it evidence of absence. C9 had the
# same shape one layer down — an unreadable diary yields an empty list, and "at most one" is
# trivially true of nothing.
# --------------------------------------------------------------------------------------


def _slots(status: int, body: object) -> Response:
    return Response(status, body, json.dumps(body), 1.0, extract_error_code(body))


def test_c3_passes_only_on_a_wellformed_empty_offer() -> None:
    control = judge_closed_day(_slots(200, {"slots": [], "availability": "ok"}))
    assert control.passed and control.ran


def test_c3_fails_when_the_offer_is_not_empty() -> None:
    slot = {"start": "2026-08-08T09:00:00Z", "end": "2026-08-08T09:30:00Z"}
    assert not judge_closed_day(_slots(200, {"slots": [slot]})).passed


@pytest.mark.parametrize(
    "response",
    [
        _slots(500, {"detail": "boom"}),
        _slots(401, {"detail": {"error": "unauthorized"}}),
        _slots(503, {"detail": {"error": "availability_unavailable"}}),
        Response(0, None, "URLError: connection refused", 1.0, "transport_error"),
    ],
)
def test_c3_fails_when_the_slots_query_fails(response: Response) -> None:
    """==A closed Saturday and a dead API both show zero slots. Only one of them is a pass.=="""
    control = judge_closed_day(response)
    assert control.ran is True
    assert control.passed is False
    assert "FAILED" in control.observed


def test_c3_fails_on_a_200_that_is_not_the_slots_contract() -> None:
    """A 200 with no `slots` list is not an empty offer — it is a changed contract."""
    control = judge_closed_day(_slots(200, {"unexpected": "shape"}))
    assert not control.passed
    assert "contract" in control.observed


@pytest.mark.parametrize("at_most", [True, False])
def test_lineage_controls_fail_on_an_incomplete_read(at_most: bool) -> None:
    """==Both C8 and C9 must refuse to judge cardinality on a diary they could not read.==

    C9 (`at_most=True`) is the dangerous one: it asks "at most one?", and an empty list satisfies
    that trivially — so before this, a 500 from the bookings endpoint PASSED the control guarding
    against a guest left holding two live appointments.
    """
    broken = DiaryRead([], complete=False, problem="GET /bookings/ answered 500")
    control = judge_lineage(
        broken,
        ident="C9",
        guards="g",
        at_most=at_most,
        race=_mutation_race(winners=1),
        original_id=SUBJECT,
    )
    assert control.ran is True
    assert control.passed is False
    assert "could not be read completely" in control.observed


def test_lineage_passes_on_a_complete_read_with_one_survivor() -> None:
    read = _diary(SUCCESSOR)
    assert judge_lineage(
        read, ident="C8", guards="g", at_most=False, race=_mutation_race(1), original_id=SUBJECT
    ).passed
    assert judge_lineage(
        read, ident="C9", guards="g", at_most=True, race=_mutation_race(2, 2), original_id=SUBJECT
    ).passed


def test_lineage_fails_when_two_live_appointments_survive() -> None:
    """The defect the pair exists to catch, on a read that IS trustworthy."""
    read = _diary("a-1", "b-2")
    assert not judge_lineage(
        read, ident="C9", guards="g", at_most=True, race=_mutation_race(2, 2), original_id=SUBJECT
    ).passed


def test_pick_micro_slot_takes_the_first_slot_that_ENDS_within_budget() -> None:
    """The budget is about when the appointment is over, not when it starts."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    starts = [
        "2026-07-29T12:01:00Z",  # ends 12:03 -> 180s away, fits
        "2026-07-29T12:03:00Z",
    ]
    assert pick_micro_slot(starts, duration_seconds=120, budget_seconds=420, now=now) == starts[0]


def test_pick_micro_slot_refuses_a_slot_that_ends_too_late() -> None:
    """==The timezone trap, as a regression test.==

    A business in `America/New_York` puts every UTC instant between 00:00 and 04:00 on the previous
    LOCAL day, so a window opened at the harness's UTC `today` returned a first slot two hours out.
    The lax version took it anyway, truncated its wait, and marked the no-show early. Refusing is
    the correct answer; the caller widens the window instead.
    """
    now = datetime(2026, 7, 29, 1, 53, tzinfo=UTC)
    two_hours_out = ["2026-07-29T04:00:00Z", "2026-07-29T04:02:00Z"]
    assert pick_micro_slot(two_hours_out, duration_seconds=120, budget_seconds=420, now=now) is None


def test_pick_micro_slot_ignores_slots_already_in_the_past() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    past = ["2026-07-29T11:00:00Z"]
    assert pick_micro_slot(past, duration_seconds=120, budget_seconds=420, now=now) is None


def test_c9_accepts_zero_survivors_but_c8_does_not() -> None:
    """A cancel may legitimately win the mixed race; a reschedule race must leave a successor."""
    read = _diary()
    assert judge_lineage(
        read, ident="C9", guards="g", at_most=True, race=_mutation_race(2, 2), original_id=SUBJECT
    ).passed
    assert not judge_lineage(
        read, ident="C8", guards="g", at_most=False, race=_mutation_race(1), original_id=SUBJECT
    ).passed


# --------------------------------------------------------------------------------------
# ==C13 — the organic phase's failures must be able to reach the verdict.==
#
# Until this control existed, two `return` statements in `run_organic` swallowed them: a slots query
# that FAILED produced no starts and was filed as `no_slots_offered` (which §1 prints as "attempts
# that met a fully-booked day"), and any non-2xx booking that was not a collision was discarded by a
# bare `if not response.ok: return`. An instance 500-ing through the whole fortnight presented as a
# slightly quieter one, and the run still stamped MEASURED.
# --------------------------------------------------------------------------------------


def _organic(planned: int = 10) -> OrganicResult:
    """A clean organic result for ``planned`` items, which a test then breaks in exactly one way."""
    result = OrganicResult()
    result.booked = [
        BookedRef(f"id{n}", "clinica-sonrisa", f"g{n}@guests.sim.test", "2026-08-03T09:00:00Z", 1.0)
        for n in range(planned)
    ]
    return result


def test_c13_passes_when_every_intent_is_explained() -> None:
    control = judge_organic_accounting(_organic(), planned=10)
    assert control.passed and control.ran


def test_c13_reconciles_the_follow_up_leg_too() -> None:
    result = _organic()
    result.follow_ups_attempted = 4
    result.cancelled = 3
    result.rescheduled = 1
    assert judge_organic_accounting(result, planned=10).passed


def test_c13_fails_when_the_follow_up_leg_loses_an_attempt() -> None:
    result = _organic()
    result.follow_ups_attempted = 4
    result.cancelled = 3
    assert not judge_organic_accounting(result, planned=10).passed


def test_c13_treats_a_reschedule_COLLISION_as_ordinary_traffic() -> None:
    """==The asymmetry that voided run 6662feb5, pinned so it cannot come back.==

    The follow-up reads the day's offer and then posts the move, so another simulated guest can take
    the chosen slot in between. That is the create leg's `collisions` one mutation later — the
    product refusing correctly — and lumping it in with the findings made C13 go red against a
    perfect system, which is the very defect class C5 and C7 were just fixed for.
    """
    result = _organic()
    result.follow_ups_attempted = 4
    result.cancelled = 1
    result.rescheduled = 1
    result.reschedule_collisions = 2
    control = judge_organic_accounting(result, planned=10)
    assert control.passed is True
    assert "reschedule_collisions': 2" in control.observed


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_slots(409, {"detail": {"error": "slot_unavailable"}}), True),
        (_slots(409, {"detail": {"error": "not_active"}}), False),
        (_slots(409, {"detail": {"error": "day_full"}}), False),
        (_slots(500, {"detail": "boom"}), False),
        (Response(0, None, "URLError: refused", 1.0, "transport_error"), False),
        (_slots(200, {"id": "x"}), False),
    ],
)
def test_only_slot_unavailable_is_an_ordinary_reschedule_collision(
    response: Response, expected: bool
) -> None:
    """==The CLASSIFIER, pinned — because the judge's tests could not reach it.==

    This rule lived inline in `run_organic`'s follow-up worker, and the only tests near it built an
    `OrganicResult` by hand. Deleting the distinction outright left every one of them green: they
    bound the judge and never the classifier. A mutation run is what surfaced that, which is the
    point of running one — *if breaking it changes nothing, nothing was checking it.*

    `not_active` and `day_full` are 409s exactly like `slot_unavailable`, so "any 409 is traffic"
    would blind the control. Only the crowding race is traffic.
    """
    assert is_reschedule_collision(response) is expected


def test_c13_still_fails_on_a_reschedule_refused_for_ANY_OTHER_reason() -> None:
    """==The line is drawn by machine code, not by status class.==

    `not_active` is a 409 exactly like `slot_unavailable`, and it would mean the lineage was already
    broken. "Any 409 is traffic" would have been the lazy fix and it would have blinded the control.
    """
    result = _organic()
    result.follow_ups_attempted = 4
    result.cancelled = 1
    result.rescheduled = 1
    result.reschedule_refused = {"not_active": 2}
    control = judge_organic_accounting(result, planned=10)
    assert control.passed is False
    assert "reschedule_refused" in control.observed


def test_c13_fails_when_a_slots_query_broke() -> None:
    """==THE defect: a failed read used to be counted as a fully-booked day.==

    The categories still add up here — nine booked plus one broken read is ten — so the arithmetic
    alone would not catch it. It is caught because a broken read is a FINDING, never traffic.
    """
    result = _organic()
    result.booked = result.booked[:9]
    result.slots_read_failed = ["clinica-sonrisa 2026-08-03: the slots query FAILED: 500 None"]
    control = judge_organic_accounting(result, planned=10)
    assert control.ran is True
    assert control.passed is False
    assert "slots_read_failed" in control.observed


def test_c13_fails_on_a_5xx_booking_that_is_not_a_collision() -> None:
    result = _organic()
    result.booked = result.booked[:8]
    result.booking_refused = {"http_500": 2}
    control = judge_organic_accounting(result, planned=10)
    assert not control.passed
    assert "booking_refused" in control.observed


def test_c13_fails_when_the_outcomes_do_not_add_up() -> None:
    """==A future silent `return` breaks the arithmetic instead of shrinking a number.==

    Nothing recorded here is a "finding" — every outcome present is ordinary traffic. The run is
    refused purely because 8 explained outcomes cannot account for 10 planned intents, which is the
    only way a count can notice an omission nobody told it about.
    """
    result = _organic()
    result.booked = result.booked[:8]
    control = judge_organic_accounting(result, planned=10)
    assert not control.passed
    assert "8/10" in control.observed


def test_c13_fails_on_a_201_that_is_not_the_booking_contract() -> None:
    result = _organic()
    result.booked = result.booked[:9]
    result.booking_unreadable = ["g9@guests.sim.test: 'not the contract'"]
    assert not judge_organic_accounting(result, planned=10).passed


def test_c13_treats_collisions_and_full_days_as_ORDINARY_traffic() -> None:
    """The control must not fire on the product working. A collision is not a defect."""
    result = _organic()
    result.booked = result.booked[:7]
    result.collisions = 2
    result.no_slots_offered = 1
    assert judge_organic_accounting(result, planned=10).passed


def test_c13_refuses_an_empty_plan() -> None:
    """Zero planned intents reconcile trivially with zero outcomes — and prove nothing."""
    assert not judge_organic_accounting(OrganicResult(), planned=0).passed


@pytest.mark.parametrize(
    "response",
    [
        _slots(500, {"detail": "boom"}),
        _slots(503, {"detail": {"error": "availability_unavailable"}}),
        Response(0, None, "URLError: connection refused", 1.0, "transport_error"),
        _slots(200, {"unexpected": "shape"}),
    ],
)
def test_read_offer_never_calls_a_broken_read_an_empty_day(response: Response) -> None:
    """==The single source of truth C3 and the organic phase now share.==

    C3 had learned this; the organic phase had not, because the judgement was inlined in C3 instead
    of extracted. A rule enforced at one of its call sites is a rule with a hole in it.
    """
    offer = read_offer(response)
    assert offer.complete is False
    assert offer.starts == []
    assert offer.problem


def test_read_offer_accepts_a_well_formed_empty_offer() -> None:
    offer = read_offer(_slots(200, {"slots": [], "availability": "ok"}))
    assert offer.complete is True
    assert offer.starts == []


# --------------------------------------------------------------------------------------
# ==C7 — a timeout and a duplication are different facts and must not read the same.==
#
# The control was `time.sleep(12)` and one read. A webhook arriving at 13 seconds produced "0 seen",
# and the control reported that as the defect it exists to detect — accusing the product of a fault
# that belonged to the harness, with no way to tell the two apart.
# --------------------------------------------------------------------------------------


def _cancel(
    counts: dict[str, int], *, drained: bool = True, unreadable: int = 0
) -> CancelWebhookObservation:
    return CancelWebhookObservation(
        counts=counts,
        drained=drained,
        drain_wait_seconds=3.0,
        appeared_after_seconds=1.0 if counts.get("booking.cancelled") else None,
        appear_timeout_seconds=60.0,
        settle_seconds=10.0,
        unreadable=unreadable,
    )


@pytest.mark.parametrize(
    ("offer", "expected"),
    [
        (OfferRead([], False, "the slots query FAILED: 500 None"), "slots_query_failed"),
        (OfferRead([], False, "200 but the body is not the slots contract"), "slots_query_failed"),
        (OfferRead([], True), "no_slots_offered"),
    ],
)
def test_cap_probe_tells_a_broken_read_from_a_day_that_left_the_offer(
    offer: OfferRead, expected: str
) -> None:
    """==The distinction C4 depends on, at the site that makes it.==

    Both produce "no slots". Only one of them is the cap biting; the other is an instrument that
    stopped answering, and C4's pass condition would have absorbed it.
    """
    blocker = cap_probe_blocker(offer)
    assert blocker is not None
    assert blocker.startswith(expected)


def test_cap_probe_lets_a_real_offer_through() -> None:
    assert cap_probe_blocker(OfferRead(["2026-08-03T09:00:00Z"], True)) is None


def test_c4_passes_when_the_third_probe_is_stopped() -> None:
    assert judge_day_cap(["201/ok", "201/ok", "no_slots_offered"]).passed
    assert judge_day_cap(["201/ok", "201/ok", "409/day_full"]).passed


def test_c4_fails_when_the_third_probe_SUCCEEDS() -> None:
    """The cap did not bite: three bookings landed on a day capped at two."""
    assert not judge_day_cap(["201/ok", "201/ok", "201/ok"]).passed


def test_c4_fails_when_a_probe_read_a_BROKEN_offer() -> None:
    """==C4 hopes for an empty offer, which is why a broken read is most dangerous here.==

    It checked `offer.ok` and then called `slot_starts` directly, so a 2xx whose body was not the
    slots contract yielded no starts and read as the cap biting. `read_offer` asks both halves of
    the question now, and this is the verdict side of that: a failed query matches neither accepted
    third outcome.
    """
    control = judge_day_cap(
        ["201/ok", "201/ok", "slots_query_failed(200 but the body is not the slots contract)"]
    )
    assert control.ran is True
    assert control.passed is False
    assert "slots_query_failed" in control.observed


def test_c7_passes_on_exactly_one_delivery() -> None:
    control = judge_cancel_idempotency(_cancel({"booking.created": 1, "booking.cancelled": 1}))
    assert control.passed and control.ran


def test_c7_fails_on_a_duplicate_which_is_the_defect_it_guards() -> None:
    control = judge_cancel_idempotency(_cancel({"booking.cancelled": 2}))
    assert not control.passed
    assert "2 booking.cancelled delivered" in control.observed


def test_c7_names_a_missing_delivery_as_a_DELIVERY_failure() -> None:
    control = judge_cancel_idempotency(_cancel({"booking.created": 1}))
    assert not control.passed
    assert "DELIVERY failure" in control.observed


def test_c7_tells_a_timeout_apart_from_a_duplication() -> None:
    """==The whole point, as one assertion.== Both fail; they must not fail the SAME way."""
    never = judge_cancel_idempotency(_cancel({}))
    duplicated = judge_cancel_idempotency(_cancel({"booking.cancelled": 2}))
    assert not never.passed and not duplicated.passed
    assert never.observed != duplicated.observed
    assert "NOTHING arrived" in never.observed
    assert "NOTHING arrived" not in duplicated.observed


def test_c7_refuses_to_certify_over_deliveries_it_could_not_READ() -> None:
    """==A duplicate hiding in an undecodable body is invisible to the count.==

    The per-booking reader used to `continue` past any delivery whose base64 or JSON it could not
    decode, which silently subtracts from the very number C7 then calls "exactly one". A broken
    reader always returns the reassuring answer -- `count_sink_events` had already learned that one
    function higher up and surfaced `unreadable`; this reader went on swallowing them.
    """
    control = judge_cancel_idempotency(_cancel({"booking.cancelled": 1}, unreadable=1))
    assert control.passed is False
    assert "could NOT be decoded" in control.observed
    assert "lower bound" in control.observed


def test_c7_reports_unreadable_deliveries_even_when_it_passes() -> None:
    """Zero is a measurement here, so it is printed rather than left to be assumed."""
    control = judge_cancel_idempotency(_cancel({"booking.cancelled": 1}))
    assert control.passed is True
    assert "unreadable deliveries 0" in control.observed


def test_c7_refuses_to_judge_when_the_outbox_never_drained() -> None:
    """An unattempted delivery is not an absent one; the observation was simply never valid."""
    control = judge_cancel_idempotency(_cancel({"booking.cancelled": 1}, drained=False))
    assert not control.passed
    assert "did NOT drain" in control.observed


# --------------------------------------------------------------------------------------
# ==C5 — the dead-man must not race the system it measures.==
#
# The old pass condition was `first_reading.due > baseline`, where `due` is an INSTANTANEOUS gauge
# and `first_reading` is the earliest scrape after the worker restarts. The restarted worker serves
# that metric AND drains the queue, so on a fast tick it can finish draining before the first scrape
# is answered — and the control then failed a system that had behaved perfectly.
# --------------------------------------------------------------------------------------


def _deadman(**overrides: Any) -> DeadmanObservation:
    base: dict[str, Any] = {
        "work_created": 6,
        "surface_unreachable": True,
        "baseline_due": 0,
        "baseline_rows": 400,
        "final_rows": 412,
        "delivered_after_restart": 12,
        "drain_recovery_seconds": 7.3,
        "first_reading_due": 6,
        "first_reading_oldest_age_seconds": 30.9,
    }
    base.update(overrides)
    return DeadmanObservation(**base)


def test_c5_passes_when_the_backlog_was_caught_in_flight() -> None:
    observation = _deadman()
    assert observation.caught_in_flight is True
    assert judge_drain_deadman(observation).passed


def test_c5_passes_when_the_drain_beat_the_first_scrape() -> None:
    """==The regression test for the race.== A perfect system used to FAIL this control by luck.

    `first_reading_due=0` is the reading a fast drain produces: the queue was already empty by the
    time the restarted worker answered. Nothing is wrong, and the durable signals — twelve new rows
    the snapshot can still see, twelve deliveries the restarted process counted — say so.
    """
    observation = _deadman(first_reading_due=0, first_reading_oldest_age_seconds=0.0)
    assert observation.caught_in_flight is False
    control = judge_drain_deadman(observation)
    assert control.passed is True
    assert "drained before the first scrape" in control.observed


def test_c5_passes_even_when_no_scrape_answered_during_the_boot_window() -> None:
    """The corroborating reading may be missing entirely; the durable ones cannot be."""
    assert judge_drain_deadman(_deadman(first_reading_due=None)).passed


def test_c5_still_fails_an_instrument_wired_to_a_constant() -> None:
    """==The failure the control exists for, and it must survive the fix.==

    A snapshot that answers the same numbers whatever the table holds shows no row growth — caught
    by a signal which, unlike `due`, cannot have quietly returned to rest before anyone looked.
    """
    control = judge_drain_deadman(_deadman(final_rows=400))
    assert not control.passed
    assert "not reading the real table" in control.observed


def test_c5_fails_when_the_worker_never_actually_stopped() -> None:
    control = judge_drain_deadman(_deadman(surface_unreachable=False))
    assert not control.passed
    assert "still ANSWERED" in control.observed


def test_c5_fails_when_the_restarted_worker_did_not_process_the_stranded_work() -> None:
    control = judge_drain_deadman(_deadman(delivered_after_restart=2))
    assert not control.passed
    assert "2 delivered" in control.observed


def test_c5_fails_when_the_delivered_counter_is_absent_rather_than_zero() -> None:
    """==A changed contract must not present as a worker that drained nothing.=="""
    control = judge_drain_deadman(_deadman(delivered_after_restart=None))
    assert not control.passed
    assert "contract changed" in control.observed


def test_c5_fails_when_the_backlog_never_cleared() -> None:
    control = judge_drain_deadman(_deadman(drain_recovery_seconds=None))
    assert not control.passed
    assert "NEVER returned to 0" in control.observed


def test_c5_fails_when_no_work_was_ever_stranded() -> None:
    """Stranding nothing and then observing nothing is not evidence of a live instrument."""
    assert not judge_drain_deadman(_deadman(work_created=0, final_rows=400)).passed


# --------------------------------------------------------------------------------------
# ==C14 — the confirmation sample is whole, or the run is void.==
#
# Two silent subtractions sat between the mailbox and §2, and both moved the published latency the
# same, flattering way: a hardcoded `limit=20000` truncated the read, and `if delta_ms >= 0` dropped
# precisely the fastest confirmations. The report even PRINTED a warning about the first — and a run
# that shipped with that warning still said MEASURED, which is the difference between prose and a
# control.
# --------------------------------------------------------------------------------------


def _coverage(**overrides: Any) -> ConfirmationCoverage:
    base: dict[str, Any] = {
        "created": 207,
        "matched": 207,
        "negative_deltas": 0,
        "worst_negative_ms": 0.0,
        "read_complete": True,
        "read_problem": "",
        "reported_total": 273,
        "page_size": 500,
        "attempts": 1,
        "waited_seconds": 0.4,
    }
    base.update(overrides)
    return ConfirmationCoverage(**base)


def test_c14_passes_on_a_whole_reconciled_sample() -> None:
    assert judge_confirmation_coverage(_coverage()).passed


def test_c14_fails_when_the_mailbox_read_was_truncated() -> None:
    """==The mailbox ceiling, as a control instead of a constant nobody compared anything to.=="""
    control = judge_confirmation_coverage(
        _coverage(read_complete=False, read_problem="an empty page at start 20000 of 24310")
    )
    assert not control.passed
    assert "INCOMPLETE" in control.observed


def test_c14_fails_when_bookings_have_no_confirmation() -> None:
    control = judge_confirmation_coverage(_coverage(matched=180))
    assert not control.passed
    assert "27 created booking(s) never matched" in control.observed


def test_c14_fails_on_a_confirmation_that_preceded_its_own_cause() -> None:
    """Referenced to the SEND instant this cannot happen legitimately, so it is real clock skew."""
    control = judge_confirmation_coverage(_coverage(negative_deltas=3, worst_negative_ms=-820.0))
    assert not control.passed
    assert "preceded the POST" in control.observed


def test_c14_refuses_a_run_that_created_nothing() -> None:
    assert not judge_confirmation_coverage(_coverage(created=0, matched=0)).passed


# --------------------------------------------------------------------------------------
# The mailbox reader itself: paging, and refusing to look SMALL when it was cut short.
# --------------------------------------------------------------------------------------


def _envelope(address: str, created: str = "2026-07-29T02:00:00.000Z") -> dict[str, Any]:
    return {"To": [{"Address": address}], "Subject": "Confirmed", "Created": created}


class _FakeMailpit:
    """Answers `/api/v1/messages` from a list, honouring `start`/`limit` the way Mailpit does."""

    def __init__(self, held: list[dict[str, Any]], *, reported_total: int | None = None) -> None:
        self.held = held
        self.reported_total = len(held) if reported_total is None else reported_total
        self.requests: list[str] = []

    def __call__(self, path: str) -> Any:
        self.requests.append(path)
        query = dict(pair.split("=", 1) for pair in path.split("?", 1)[1].split("&") if "=" in pair)
        start = int(query["start"])
        limit = int(query["limit"])
        return {
            "messages_count": self.reported_total,
            "start": start,
            "messages": self.held[start : start + limit],
        }


def _mailbox(fake: _FakeMailpit, *, page_size: int = 500) -> Mailbox:
    mailbox = Mailbox("http://localhost:8025", page_size=page_size)
    mailbox._get = fake  # type: ignore[method-assign]  # the transport is exactly what we stub
    return mailbox


def test_mailbox_pages_past_its_page_size() -> None:
    """==A mailbox bigger than one request is READ, not truncated.==

    The old reader asked for `limit=20000` once. Past that the list simply stopped, and a latency
    sample that loses members reports a faster product — a ceiling whose failure mode was to
    improve the numbers.
    """
    fake = _FakeMailpit([_envelope(f"g{n}@guests.sim.test") for n in range(1200)])
    read = _mailbox(fake, page_size=500).read_all()
    assert read.complete is True
    assert len(read.messages) == 1200
    assert read.pages == 3
    assert read.reported_total == 1200


def test_mailbox_reports_an_incomplete_read_rather_than_a_short_list() -> None:
    """The server says it holds 5000 and hands back 100. A broken read, not a small mailbox."""
    fake = _FakeMailpit(
        [_envelope(f"g{n}@guests.sim.test") for n in range(100)], reported_total=5000
    )
    read = _mailbox(fake, page_size=500).read_all()
    assert read.complete is False
    assert "empty page at start 100 of a reported total of 5000" in read.problem


def test_mailbox_read_that_cannot_reconcile_is_incomplete() -> None:
    """An envelope carrying neither total is a CHANGED CONTRACT, never a mailbox of zero."""
    mailbox = Mailbox("http://localhost:8025")
    mailbox._get = lambda path: {"messages": []}  # type: ignore[method-assign]
    read = mailbox.read_all()
    assert read.complete is False
    assert "contract changed" in read.problem


def test_mailbox_read_survives_a_failed_request_as_a_fact() -> None:
    def boom(path: str) -> Any:
        raise OSError("connection refused")

    mailbox = Mailbox("http://localhost:8025")
    mailbox._get = boom  # type: ignore[method-assign]
    read = mailbox.read_all()
    assert read.complete is False
    assert "connection refused" in read.problem
    assert read.messages == []


def test_mailbox_counts_unreadable_envelopes_instead_of_dropping_them() -> None:
    fake = _FakeMailpit(
        [
            _envelope("good@guests.sim.test"),
            {"To": [{"Address": "nostamp@guests.sim.test"}], "Created": "not-a-date"},
            {"To": [], "Created": "2026-07-29T02:00:00Z"},
        ]
    )
    read = _mailbox(fake).read_all()
    assert read.complete is True
    assert len(read.messages) == 1
    assert read.unparseable == 2


# --------------------------------------------------------------------------------------
# ==The latency bias, end to end: a confirmation that beat its own 201 must be COUNTED.==
# --------------------------------------------------------------------------------------


CONFIRMED_INVITE = CalendarInvite(
    uid="uid-1@aethercal", method="REQUEST", status="CONFIRMED", sequence=0
)
CANCELLED_INVITE = CalendarInvite(
    uid="uid-1@aethercal", method="CANCEL", status="CANCELLED", sequence=1
)


class _StubMailbox:
    """A mailbox that hands back one prepared read, and counts how many times it was asked."""

    def __init__(self, read: MailboxRead) -> None:
        self.read = read
        self.reads = 0

    def read_all(self) -> MailboxRead:
        self.reads += 1
        return self.read

    def hydrate_invites(self, messages: list[MailMessage]) -> tuple[list[MailMessage], str]:
        """Already hydrated by the fixture: these tests declare each message's invite directly."""
        return messages, ""


def test_a_confirmation_that_arrives_before_the_201_returns_is_now_measured() -> None:
    """==The regression test for the latency bias, stated as the thing that was thrown away.==

    The POST is sent at 12:00:00.000 and the worker has Mailpit stamp the confirmation at
    12:00:00.050 — 50 ms later, while the guest's own HTTP response may still be in flight. Against
    the OLD reference (the instant the 201 came back) that produced a NEGATIVE delta, dropped by
    `if delta_ms >= 0`. Only the fastest confirmations can precede their own response, so the
    discard trimmed the left tail and pushed every published percentile up.
    """
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("id1", "b", "g1@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [
            MailMessage(
                "g1@guests.sim.test",
                "Confirmed",
                sent + timedelta(milliseconds=50),
                message_id="m1",
                invite=CONFIRMED_INVITE,
            )
        ],
        True,
        1,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(
        _StubMailbox(read),  # type: ignore[arg-type]
        booked,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert coverage.matched == 1
    assert coverage.negative_deltas == 0
    assert latency.count == 1
    assert latency.samples[0] == pytest.approx(50.0, abs=1.0)


def test_a_genuinely_negative_delta_is_counted_and_voids_rather_than_vanishing() -> None:
    """Against the send instant this cannot be legitimate: skew, reported rather than filtered."""
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("id1", "b", "g1@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [
            MailMessage(
                "g1@guests.sim.test",
                "Confirmed",
                sent - timedelta(seconds=2),
                message_id="m1",
                invite=CONFIRMED_INVITE,
            )
        ],
        True,
        1,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(
        _StubMailbox(read),  # type: ignore[arg-type]
        booked,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert coverage.negative_deltas == 1
    assert coverage.worst_negative_ms == pytest.approx(-2000.0, abs=1.0)
    assert latency.count == 0
    assert not judge_confirmation_coverage(coverage).passed


def test_an_incomplete_mailbox_read_is_not_retried_for_ever() -> None:
    """A broken read cannot be improved by asking again; it is returned as the fact it is."""
    read = MailboxRead([], False, 5000, 1, 500, 0, "GET failed: OSError: refused")
    stub = _StubMailbox(read)
    _latency, out, coverage = measure_confirmations(
        stub,  # type: ignore[arg-type]
        [BookedRef("id1", "b", "g1@guests.sim.test", "s", 1.0)],
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert out.complete is False
    assert stub.reads == 1
    assert coverage.attempts == 1
    assert not judge_confirmation_coverage(coverage).passed


# --------------------------------------------------------------------------------------
# ==The CLI cannot be asked for a run that measures nothing.==
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["0", "-1", "-40"])
def test_the_run_shape_refuses_non_positive_values(raw: str) -> None:
    """`--contenders 0` builds a `Barrier(0)`; `--workers 0` a pool of none."""
    with pytest.raises(argparse.ArgumentTypeError, match="1 or more"):
        positive_int(raw)


def test_the_run_shape_refuses_a_non_integer() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="not an integer"):
        positive_int("eight")


@pytest.mark.parametrize("flag", ["--workers", "--contenders", "--per-week"])
def test_the_cli_exits_2_rather_than_running_a_degenerate_simulation(flag: str) -> None:
    """==Exit 2, not a traceback and emphatically not a green run.==

    `--contenders 1` is the nastiest of these: it would not crash. A one-way race has exactly one
    winner whatever the product does, so C10 would pass and C2 -- the control whose whole job is to
    prove the harness can see MORE than one winner -- would be firing a single request at a single
    slot and confirming it.
    """
    with pytest.raises(SystemExit) as excinfo:
        parse_args([flag, "0"])
    assert excinfo.value.code == 2


def test_the_cli_accepts_the_documented_defaults() -> None:
    args = parse_args([])
    assert (args.workers, args.contenders, args.per_week, args.seed) == (8, 40, 40, 20260725)


@pytest.mark.parametrize("raw", ["0", "1", "-3"])
def test_a_race_needs_at_least_two_contenders(raw: str) -> None:
    """==The defect the previous commit DESCRIBED and did not guard.==

    `--contenders 1` does not crash, which is what makes it the dangerous value: `Barrier(1)`
    releases immediately, every race reports "exactly one winner" whatever the product does, and
    C2 -- the control that exists to prove more than one winner is observable -- fires a single
    request at a single slot and confirms it. The run reads MEASURED having tested no concurrency.
    """
    with pytest.raises(argparse.ArgumentTypeError):
        contender_count(raw)


def test_one_contender_is_refused_by_the_cli_with_exit_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--contenders", "1"])
    assert excinfo.value.code == 2


def test_two_contenders_is_the_smallest_real_race() -> None:
    assert contender_count("2") == 2
    assert parse_args(["--contenders", "2"]).contenders == 2


# --------------------------------------------------------------------------------------
# ==The metrics reader: an absent field is NOT a healthy one.==
#
# The module's own docstring has always said "a failed scrape is recorded as a failure, never as a
# zero" -- and the reader did the opposite one screen down. `outbox.get("due", 0)`,
# `oldest_due_age_seconds` defaulting to 0.0 and a `by_status` falling back to `{}` turned every
# absent field into the healthiest possible value, so a renamed field in a future version, a partial
# response, or an error serialised as JSON all read as BACKLOG 0: C12 would announce "drained", §3
# would print a flat line, and C5 would have nothing to see. ==A backlog that reaches zero because
# the field disappeared is indistinguishable from a system that is keeping up.==
# --------------------------------------------------------------------------------------


def _summary(**outbox_overrides: Any) -> dict[str, Any]:
    """A well-formed payload, shaped like the one the live worker serves."""
    outbox: dict[str, Any] = {
        "by_status": dict.fromkeys(EXPECTED_OUTBOX_STATUSES, 0),
        "due": 0,
        "oldest_due_age_seconds": 0.0,
        "expired_leases": 0,
    }
    outbox.update(outbox_overrides)
    return {"outbox": outbox, "drain": {"delivered": 0, "passes": 1, "lost": 0}}


def test_a_well_formed_payload_parses() -> None:
    sample = parse_summary(_summary(due=7, oldest_due_age_seconds=3.5))
    assert sample.due == 7
    assert sample.oldest_due_age_seconds == 3.5
    assert sample.delivered == 0


def test_the_real_worker_payload_parses_unchanged() -> None:
    """==Pins that the strict reader agrees with the permissive one on a GOOD payload.==

    Captured from the live worker during a run: eight statuses, integer counts, a float age, and
    `drain.delivered`. If the strict parse of this differed from what the permissive reader
    produced, the published numbers would have moved and the run would owe a re-run. It does not.
    """
    observed: dict[str, Any] = {
        "outbox": {
            "by_status": {
                "pending": 230,
                "claimed": 0,
                "failed": 0,
                "delivered": 290,
                "dead": 0,
                "skipped": 0,
                "voided": 37,
                "unknown": 0,
            },
            "due": 0,
            "oldest_due_age_seconds": 0.0,
            "expired_leases": 0,
        },
        "drain": {"lost": 0, "passes": 39, "delivered": 290, "failed": 0, "dead": 0},
    }
    sample = parse_summary(observed)
    assert sample.due == 0
    assert sample.rows == 557
    assert sample.delivered == 290


@pytest.mark.parametrize("missing", ["due", "oldest_due_age_seconds", "by_status"])
def test_an_ABSENT_outbox_field_is_a_scrape_error_not_a_zero(missing: str) -> None:
    """==The finding, one assertion per field.== Absent must never deserialise to healthy."""
    payload = _summary()
    del payload["outbox"][missing]
    with pytest.raises(OutboxScrapeError, match=missing):
        parse_summary(payload)


@pytest.mark.parametrize("bad", [None, "0", "", [], {}, 3.5, True, -1])
def test_a_due_of_the_wrong_TYPE_is_a_scrape_error(bad: object) -> None:
    """No permissive coercion: a string is a server that changed how it serialises, not a zero.

    `True` is in here deliberately -- `bool` is a subclass of `int`, so an unguarded `isinstance`
    check would have let it through as 1.
    """
    with pytest.raises(OutboxScrapeError):
        parse_summary(_summary(due=bad))


@pytest.mark.parametrize("bad", [None, "3.5", [], True, -0.5])
def test_an_oldest_due_age_of_the_wrong_TYPE_is_a_scrape_error(bad: object) -> None:
    """The oldest-due age IS the dead-man alarm; a bad value must not silence it with 0.0."""
    with pytest.raises(OutboxScrapeError):
        parse_summary(_summary(oldest_due_age_seconds=bad))


@pytest.mark.parametrize("bad", [None, "{}", [], 7, "pending"])
def test_a_by_status_that_is_not_an_object_is_a_scrape_error(bad: object) -> None:
    """It fell back to an empty map, and `rows` -- C5's durable signal -- sums that to 0."""
    with pytest.raises(OutboxScrapeError, match="by_status"):
        parse_summary(_summary(by_status=bad))


@pytest.mark.parametrize("bad", [None, "3", 3.0, True, -2])
def test_a_by_status_VALUE_is_validated_too(bad: object) -> None:
    """==Not enough that the key exists.== A null or a string where an integer goes is an error.

    This is the level the zero moves down to when only the key is checked: the map is present, the
    status is there, the shape looks right -- and one series quietly counts as nothing.
    """
    statuses: dict[str, Any] = dict.fromkeys(EXPECTED_OUTBOX_STATUSES, 0)
    statuses["delivered"] = bad
    with pytest.raises(OutboxScrapeError):
        parse_summary(_summary(by_status=statuses))


def test_a_MISSING_status_is_a_scrape_error() -> None:
    """`rows` is the sum of these and C5 compares it to work created; a lost series changes it."""
    statuses: dict[str, Any] = dict.fromkeys(EXPECTED_OUTBOX_STATUSES, 0)
    del statuses["voided"]
    with pytest.raises(OutboxScrapeError, match="voided"):
        parse_summary(_summary(by_status=statuses))


def test_an_UNRECOGNISED_status_is_a_scrape_error() -> None:
    """A status this harness does not know would be summed into `rows` without anyone deciding."""
    statuses: dict[str, Any] = dict.fromkeys(EXPECTED_OUTBOX_STATUSES, 0)
    statuses["quarantined"] = 5
    with pytest.raises(OutboxScrapeError, match="quarantined"):
        parse_summary(_summary(by_status=statuses))


def test_an_absent_drain_delivered_stays_None_rather_than_zero() -> None:
    """==C5 depends on this three-way reading.== Absent is a changed contract, not a lazy worker."""
    payload = _summary()
    del payload["drain"]
    assert parse_summary(payload).delivered is None
    payload = _summary()
    del payload["drain"]["delivered"]
    assert parse_summary(payload).delivered is None


def test_a_mistyped_drain_delivered_is_an_error_not_an_absence() -> None:
    """A quoted number is not "the field is missing"; it is the contract changing underneath."""
    payload = _summary()
    payload["drain"]["delivered"] = "12"
    with pytest.raises(OutboxScrapeError, match="delivered"):
        parse_summary(payload)


@pytest.mark.parametrize("payload", [None, [], "ok", 7, {"bookings": {}}])
def test_a_body_without_the_outbox_contract_is_a_scrape_error(payload: object) -> None:
    with pytest.raises(OutboxScrapeError):
        parse_summary(payload)


# --------------------------------------------------------------------------------------
# ==`pause()` must not return while a scrape is still in flight.==
#
# It set the flag and returned instantly, while the sampler thread could be mid-scrape -- a window
# as wide as the 10s socket timeout. `control_drain_deadman` calls pause() and stops the worker on
# the very next line, so that in-flight scrape fails with a connection refused and lands in
# `failures`. C12 gates on `scrape_failures == 0`. ==A perfectly correct run would then fail its own
# control, at random, because of an outage it caused on purpose== -- the same shape as C5 racing its
# own drain, one layer down in the instrument.
#
# Driven with explicit handshakes rather than sleeps: the scrape blocks until the test releases it,
# so the interleaving under test is the one that happens, every time.
# --------------------------------------------------------------------------------------


class _BlockingSampler(OutboxSampler):
    """A sampler whose scrape blocks until released, so the race can be staged deterministically."""

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:1", "t" * 40, interval_seconds=0.01)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.raise_scrape_error = True
        self.calls = 0

    def scrape(self) -> OutboxSample:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        if self.raise_scrape_error:
            raise OutboxScrapeError("connection refused (the deliberate outage)")
        return OutboxSample(at=0.0, due=0, oldest_due_age_seconds=0.0, by_status={}, delivered=0)


def _stage_scrape_in_flight(sampler: _BlockingSampler) -> threading.Thread:
    """Start one loop iteration and block inside its scrape."""
    worker = threading.Thread(target=sampler._scrape_once, daemon=True)
    worker.start()
    assert sampler.entered.wait(timeout=5), "the staged scrape never started"
    return worker


def test_pause_waits_for_an_in_flight_scrape_before_returning() -> None:
    """==pause() must be a barrier, not a flag.== When it returns, nothing is still scraping."""
    sampler = _BlockingSampler()
    worker = _stage_scrape_in_flight(sampler)

    returned = threading.Event()

    def do_pause() -> None:
        sampler.pause()
        returned.set()

    pauser = threading.Thread(target=do_pause, daemon=True)
    pauser.start()
    # While the scrape is blocked, pause() must NOT have returned.
    assert not returned.wait(timeout=0.3), "pause() returned while a scrape was still in flight"

    sampler.release.set()
    assert returned.wait(timeout=5), "pause() never returned after the scrape finished"
    worker.join(timeout=5)
    pauser.join(timeout=5)


def test_a_failure_from_the_deliberate_outage_is_NOT_counted_against_c12() -> None:
    """==The defect, as the number C12 actually reads.==

    The scrape is in flight, the pause begins (as the dead-man control does immediately before
    stopping the worker), and the scrape then fails. That failure belongs to the outage the run
    caused on purpose, so it must not appear in `failures` -- which C12 requires to be zero.
    """
    sampler = _BlockingSampler()
    worker = _stage_scrape_in_flight(sampler)

    pauser = threading.Thread(target=sampler.pause, daemon=True)
    pauser.start()
    time.sleep(0.05)  # let pause() set the flag and block on the in-flight lock
    sampler.release.set()
    worker.join(timeout=5)
    pauser.join(timeout=5)

    assert sampler.failures == []
    assert sampler.discarded_at_pause == 1
    control = control_outbox_drained(
        drained=True, waited_seconds=0.0, scrape_failures=len(sampler.failures)
    )
    assert control.passed is True


def test_a_sample_completing_after_a_pause_is_discarded_and_COUNTED() -> None:
    """A boundary sample is dropped -- but the drop is a reported fact, never a silent favour."""
    sampler = _BlockingSampler()
    sampler.raise_scrape_error = False
    worker = _stage_scrape_in_flight(sampler)

    pauser = threading.Thread(target=sampler.pause, daemon=True)
    pauser.start()
    time.sleep(0.05)
    sampler.release.set()
    worker.join(timeout=5)
    pauser.join(timeout=5)

    assert sampler.samples == []
    assert sampler.discarded_at_pause == 1


def test_a_real_failure_while_NOT_paused_still_reaches_c12() -> None:
    """==The fix must not become a blanket excuse.== Unpaused, a failure still counts and voids."""
    sampler = _BlockingSampler()
    worker = _stage_scrape_in_flight(sampler)
    sampler.release.set()
    worker.join(timeout=5)

    assert len(sampler.failures) == 1
    assert sampler.discarded_at_pause == 0
    control = control_outbox_drained(
        drained=True, waited_seconds=0.0, scrape_failures=len(sampler.failures)
    )
    assert control.passed is False
    assert "scrape failures=1" in control.observed


# --------------------------------------------------------------------------------------
# ==C15 -- the bursts really overlapped, which no winner count can establish.==
#
# C2 fires the same code path at N distinct slots and demands N winners, and that was presented as
# proof the harness "did not only ever really send one". It is not: booking N different slots
# strictly one after another also yields N winners. And the same blind spot covers the headline --
# a serialised harness booking ONE slot N times in sequence leaves exactly one winner too, so C10
# passes, C2 passes, and 4 reports a 40-way burst that never happened.
# --------------------------------------------------------------------------------------


def _race(name: str, intervals: list[tuple[float, float]], *, winners: int = 1) -> RaceOutcome:
    return RaceOutcome(
        name=name,
        contenders=max(len(intervals), 1),
        winners=winners,
        refusals_by_code={},
        unexpected=[],
        latency=Latency(name),
        intervals=intervals,
    )


def test_peak_overlap_counts_simultaneous_intervals() -> None:
    assert peak_overlap([(0.0, 1.0), (0.5, 1.5), (0.9, 2.0)]) == 3
    assert peak_overlap([(0.0, 1.0), (0.5, 1.5)]) == 2
    assert peak_overlap([(0.0, 1.0)]) == 1
    assert peak_overlap([]) == 0


def test_peak_overlap_of_a_strictly_serial_burst_is_one() -> None:
    """==The signature of a harness that queued instead of racing.=="""
    serial = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert peak_overlap(serial) == 1


def test_c15_passes_on_a_genuinely_concurrent_burst() -> None:
    overlapping = [(0.0, 1.0), (0.01, 1.02), (0.02, 0.98)]
    control = judge_race_concurrency([_race("race_same_slot", overlapping)])
    assert control.passed is True
    assert "race_same_slot" in control.observed


def test_c15_FAILS_a_serialised_burst_that_every_other_control_would_pass() -> None:
    """==The defect, stated as the run that would otherwise read green.==

    These are the timings of a harness whose threads never overlapped. Its winner counts are
    exactly what a correct 40-way race produces, so C2 and C10 are satisfied by it.
    """
    serial = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    control = judge_race_concurrency([_race("race_same_slot", serial)])
    assert control.ran is True
    assert control.passed is False
    assert "QUEUE, not a race" in control.observed


def test_c15_fails_if_ANY_contended_race_serialised() -> None:
    """One good race does not vouch for the others; each burst carries its own claim."""
    good = _race("control_race_distinct_slots", [(0.0, 1.0), (0.1, 1.1)])
    bad = _race("race_cancel_same_booking", [(5.0, 6.0), (6.0, 7.0)])
    control = judge_race_concurrency([good, bad])
    assert control.passed is False
    assert "race_cancel_same_booking" in control.observed


def test_c15_fails_when_a_race_recorded_no_timing_at_all() -> None:
    """==Absent evidence is not evidence.== An untimed burst cannot support the claim either."""
    control = judge_race_concurrency([_race("race_same_slot", [])])
    assert control.passed is False


def test_c15_ignores_a_single_contender_race() -> None:
    """A lone call cannot overlap with anything and is not asked to."""
    solo = RaceOutcome(
        name="solo",
        contenders=1,
        winners=1,
        refusals_by_code={},
        unexpected=[],
        latency=Latency("solo"),
        intervals=[(0.0, 1.0)],
    )
    good = _race("race_same_slot", [(0.0, 1.0), (0.1, 1.1)])
    assert judge_race_concurrency([solo, good]).passed is True


def test_c15_refuses_a_run_with_no_contended_race_at_all() -> None:
    assert judge_race_concurrency([]).passed is False


def test_race_outcome_exposes_its_own_peak() -> None:
    assert _race("r", [(0.0, 1.0), (0.5, 1.5)]).peak_overlap == 2


def test_fire_together_RECORDS_the_overlap_it_creates() -> None:
    """==The RECORDER, pinned -- because the judge's tests could not reach it.==

    `test_c15_fails_when_a_race_recorded_no_timing_at_all` builds a RaceOutcome with no intervals by
    hand, so deleting the `intervals.append(...)` line left it green: it bound the judge and never
    the recording. Same shape as `read_offer`, `is_reschedule_collision` and `cap_probe_blocker` --
    a mutation run is what surfaces it every time.

    This drives the real barrier and the real thread pool over trivial in-process callables (no
    socket), so the overlap it asserts is one the harness actually produced.
    """

    def make() -> Callable[[], Response]:
        def call() -> Response:
            time.sleep(0.05)
            return Response(201, {"id": "x"}, "", 50.0, None)

        return call

    outcome = _fire_together([make() for _ in range(4)], "probe_race")
    assert len(outcome.intervals) == 4
    assert outcome.winners == 4
    # The barrier releases all four together and each holds for 50ms, so they must overlap.
    assert outcome.peak_overlap >= 2
    assert judge_race_concurrency([outcome]).passed is True


# ==Excess padding ("AAAA====") is NOT in this list, and the reason is worth keeping.== It raised
# on the author's Windows interpreter and did not on Linux, so the case went green locally and red
# in CI -- on the platform the harness actually runs on. Over-padding is implementation-defined in
# `binascii`, which makes it a bad example of corruption rather than a bug in the guard: the cases
# below all carry characters outside the base64 alphabet, which `validate=True` rejects everywhere.
@pytest.mark.parametrize("corrupt", ["not base64 at all!!", "YWJj*&^%", "###", "a b c d"])
def test_a_corrupt_delivery_body_RAISES_rather_than_decoding_to_garbage(corrupt: str) -> None:
    """==The lax decoder does not fail, it lies.==

    `b64decode` defaults to `validate=False` and DISCARDS characters outside the alphabet, so a
    corrupted body decodes to plausible garbage: nothing raises, so it is never counted unreadable,
    and it does not contain the booking id either, so it reads as "a delivery that is not ours" --
    the one category C7 never looks at. A corrupted duplicate would vanish into it.
    """
    with pytest.raises(ValueError):
        decode_delivery(corrupt)


def test_a_well_formed_delivery_body_still_decodes() -> None:
    payload = b'{"event": "booking.cancelled"}'
    assert decode_delivery(base64.b64encode(payload).decode()) == payload


# --------------------------------------------------------------------------------------
# ==C8/C9 -- a control that can pass over an IMMOBILE system guards nothing.==
#
# If every request in the mutation race failed, the ORIGINAL booking is still sitting there, and a
# diary holding exactly one live appointment is precisely what "one successor survives" looks like.
# C8 passed. C9's "at most one" passed even more easily. ==The control measured a final state
# compatible with two different histories, and only one of them is the history it claims to test==
# -- the same defect as C2 being read as proof of simultaneity, one control over.
# --------------------------------------------------------------------------------------

SUBJECT = "subject-0000"
SUCCESSOR = "successor-9999"


def _mutation_race(winners: int, contenders: int = 40) -> RaceOutcome:
    return RaceOutcome(
        name="race_reschedule_same_booking",
        contenders=contenders,
        winners=winners,
        refusals_by_code={"not_active": contenders - winners} if contenders > winners else {},
        unexpected=[],
        latency=Latency("race"),
        intervals=[(0.0, 1.0), (0.01, 1.01)],
    )


def _diary(*bookings: str, complete: bool = True, problem: str = "") -> DiaryRead:
    return DiaryRead(
        [ActiveBooking(booking, "confirmed") for booking in bookings],
        complete=complete,
        problem=problem,
    )


def test_c8_passes_when_a_real_SUCCESSOR_survives() -> None:
    """==The anti-vacuity half.== A normal race with a real successor must stay green."""
    control = judge_lineage(
        _diary(SUCCESSOR),
        ident="C8",
        guards="g",
        at_most=False,
        race=_mutation_race(winners=1),
        original_id=SUBJECT,
    )
    assert control.passed is True
    assert control.ran is True


def test_c8_FAILS_when_every_mutation_failed_and_the_original_survives() -> None:
    """==The fail-open, stated as the run that used to read green.==

    Zero winners: not one reschedule took effect. The diary then holds exactly one live
    appointment -- the subject itself -- which is byte-for-byte what a correct race leaves. The old
    control saw `len(active) == 1` and passed.
    """
    control = judge_lineage(
        _diary(SUBJECT),
        ident="C8",
        guards="g",
        at_most=False,
        race=_mutation_race(winners=0),
        original_id=SUBJECT,
    )
    assert control.ran is True
    assert control.passed is False
    assert "not exactly" in control.observed
    assert "survivor IS the original subject" in control.observed


def test_c9_FAILS_when_every_mutation_failed_and_the_original_survives() -> None:
    """C9's "at most one" is even easier to satisfy over a system that never moved."""
    control = judge_lineage(
        _diary(SUBJECT),
        ident="C9",
        guards="g",
        at_most=True,
        race=_mutation_race(winners=0, contenders=2),
        original_id=SUBJECT,
    )
    assert control.passed is False
    assert "NOTHING mutated" in control.observed
    assert "survivor IS the original subject" in control.observed


def test_c9_passes_when_the_cancel_won_and_nothing_survives() -> None:
    """Zero survivors is legitimate for the mixed race -- and it proves motion by itself."""
    control = judge_lineage(
        _diary(),
        ident="C9",
        guards="g",
        at_most=True,
        race=_mutation_race(winners=2, contenders=2),
        original_id=SUBJECT,
    )
    assert control.passed is True


def test_c9_passes_when_the_reschedule_won_and_a_successor_survives() -> None:
    control = judge_lineage(
        _diary(SUCCESSOR),
        ident="C9",
        guards="g",
        at_most=True,
        race=_mutation_race(winners=2, contenders=2),
        original_id=SUBJECT,
    )
    assert control.passed is True


def test_c9_still_fails_on_two_live_appointments() -> None:
    """The invariant the pair exists for, undisturbed by the new conditions."""
    control = judge_lineage(
        _diary(SUCCESSOR, "another-1111"),
        ident="C9",
        guards="g",
        at_most=True,
        race=_mutation_race(winners=2, contenders=2),
        original_id=SUBJECT,
    )
    assert control.passed is False
    assert "double-booked" in control.observed


def test_c8_still_fails_when_no_successor_survives() -> None:
    """A pure reschedule race must leave a successor; zero is a lost booking."""
    control = judge_lineage(
        _diary(),
        ident="C8",
        guards="g",
        at_most=False,
        race=_mutation_race(winners=1),
        original_id=SUBJECT,
    )
    assert control.passed is False


def test_c8_fails_when_the_reschedule_race_had_more_than_one_winner() -> None:
    """Exactly one may win by contract; two means the advisory lock did not hold."""
    control = judge_lineage(
        _diary(SUCCESSOR),
        ident="C8",
        guards="g",
        at_most=False,
        race=_mutation_race(winners=2),
        original_id=SUBJECT,
    )
    assert control.passed is False


@pytest.mark.parametrize("at_most", [True, False])
def test_lineage_refuses_to_judge_without_the_race(at_most: bool) -> None:
    """==Absent evidence is not evidence== -- with no race outcome nothing can be evidenced."""
    control = judge_lineage(
        _diary(SUCCESSOR),
        ident="C8",
        guards="g",
        at_most=at_most,
        race=None,
        original_id=SUBJECT,
    )
    assert control.passed is False
    assert "not available" in control.observed


# --------------------------------------------------------------------------------------
# ==S16 -- any mail to the recipient counted as a confirmation.==
#
# "Matched" meant *the earliest message to that address*. Every guest also receives a cancellation
# or reschedule notice, so the rule worked only because confirmations normally arrive first -- ==a
# property of the ORDER OF EVENTS, not a check.== In the case that matters (confirmation never sent,
# cancellation sent) the earliest and only message is the cancellation: the booking counted as
# confirmed, C14 certified a COMPLETE sample over a hole in it, and 2 published the delay of a
# message sent minutes later for another reason. A delivery failure published as a high latency.
# --------------------------------------------------------------------------------------


def _msg(address: str, when: datetime, invite: CalendarInvite | None, ident: str) -> MailMessage:
    return MailMessage(address, "subject", when, message_id=ident, invite=invite)


def _invite(uid: str, method: str, status: str, sequence: int) -> CalendarInvite:
    return CalendarInvite(uid=uid, method=method, status=status, sequence=sequence)


def test_only_the_CONFIRMATION_is_grouped_not_the_later_notices() -> None:
    """==The anti-vacuity half: the normal flow still pairs exactly one confirmation.==

    A guest who books and then cancels receives two messages. Only the first is a confirmation, and
    the cancellation must not be counted as one.
    """
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    messages = [
        _msg("g@guests.sim.test", now, _invite("u1", "REQUEST", "CONFIRMED", 0), "m1"),
        _msg(
            "g@guests.sim.test",
            now + timedelta(minutes=5),
            _invite("u1", "CANCEL", "CANCELLED", 1),
            "m2",
        ),
    ]
    grouped = confirmations_by_recipient(messages)
    assert list(grouped) == ["g@guests.sim.test"]
    assert [m.message_id for m in grouped["g@guests.sim.test"]] == ["m1"]


def test_a_RESCHEDULE_notice_is_not_a_confirmation() -> None:
    """A reschedule re-invites the SAME uid with a bumped sequence; the sequence separates them."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    grouped = confirmations_by_recipient(
        [_msg("g@guests.sim.test", now, _invite("u1", "REQUEST", "CONFIRMED", 3), "m1")]
    )
    assert grouped == {}


def test_a_message_with_no_calendar_part_is_not_a_confirmation() -> None:
    """A reminder or a workflow email carries no `.ics` at all."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    assert confirmations_by_recipient([_msg("g@guests.sim.test", now, None, "m1")]) == {}


def test_a_MISSING_confirmation_with_a_cancellation_present_is_NOT_matched() -> None:
    """==THE sabotage case: the confirmation was never sent, the cancellation was.==

    Under the old rule this booking counted as confirmed and 2 absorbed the cancellation's
    timestamp -- minutes later, for another reason. Now it is simply unmatched, and C14 says so.
    """
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("b1", "biz", "g@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [
            _msg(
                "g@guests.sim.test",
                sent + timedelta(minutes=7),
                _invite("u1", "CANCEL", "CANCELLED", 1),
                "m1",
            )
        ],
        True,
        1,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(
        _StubMailbox(read),  # type: ignore[arg-type]
        booked,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert coverage.matched == 0
    assert coverage.created == 1
    assert latency.count == 0, "the cancellation's timestamp must not enter the distribution"
    control = judge_confirmation_coverage(coverage)
    assert control.passed is False
    assert "never matched a confirmation" in control.observed


def test_the_normal_flow_matches_ONE_confirmation_and_ignores_the_cancellation() -> None:
    """The other half: confirmation + later cancellation to the same guest still pairs cleanly."""
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("b1", "biz", "g@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [
            _msg(
                "g@guests.sim.test",
                sent + timedelta(milliseconds=800),
                _invite("u1", "REQUEST", "CONFIRMED", 0),
                "m1",
            ),
            _msg(
                "g@guests.sim.test",
                sent + timedelta(minutes=7),
                _invite("u1", "CANCEL", "CANCELLED", 1),
                "m2",
            ),
        ],
        True,
        2,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(
        _StubMailbox(read),  # type: ignore[arg-type]
        booked,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert coverage.matched == 1
    assert latency.count == 1
    assert latency.samples[0] == pytest.approx(800.0, abs=5.0)
    assert judge_confirmation_coverage(coverage).passed is True


def test_two_confirmations_to_one_recipient_break_the_one_to_one_pairing() -> None:
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("b1", "biz", "g@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [
            _msg("g@guests.sim.test", sent, _invite("u1", "REQUEST", "CONFIRMED", 0), "m1"),
            _msg("g@guests.sim.test", sent, _invite("u2", "REQUEST", "CONFIRMED", 0), "m2"),
        ],
        True,
        2,
        1,
        500,
    )
    _latency, _out, coverage = measure_confirmations(
        _StubMailbox(read),  # type: ignore[arg-type]
        booked,
        timeout_seconds=0.0,
        poll_seconds=0.0,
    )
    assert coverage.duplicate_confirmations == 1
    control = judge_confirmation_coverage(coverage)
    assert control.passed is False
    assert "not one-to-one" in control.observed


def test_the_ics_parser_reads_a_real_confirmation_and_ignores_prose() -> None:
    """==Line-anchored and exact.== A DESCRIPTION mentioning a status must not be read as one."""
    ics = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        "UID:abc-123@aethercal\r\nSTATUS:CONFIRMED\r\nSEQUENCE:0\r\n"
        "DESCRIPTION:this mentions STATUS:CANCELLED inside the prose\r\n"
        "END:VEVENT\r\nEND:VCALENDAR"
    )
    source = (
        "MIME-Version: 1.0\r\nContent-Type: text/calendar; charset=utf-8\r\n"
        "Subject: Booking confirmed\r\n\r\n" + ics
    )
    invite = Mailbox.parse_invite(source)
    assert invite is not None
    assert invite.uid == "abc-123@aethercal"
    assert invite.status == "CONFIRMED"
    assert invite.is_confirmation is True


def test_the_ics_parser_survives_a_base64_encoded_part() -> None:
    """==The reason this uses the stdlib email parser and not a regex over the source.==

    The `.ics` arrives as a MIME part that may be base64-encoded, so scanning the raw text for
    `UID:` finds nothing on exactly the messages that matter.
    """
    ics = (
        "BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\n"
        "UID:xyz-9@aethercal\r\nSTATUS:CANCELLED\r\nSEQUENCE:2\r\nEND:VEVENT\r\nEND:VCALENDAR"
    )
    encoded = base64.b64encode(ics.encode()).decode()
    source = (
        "MIME-Version: 1.0\r\nContent-Type: text/calendar; charset=utf-8\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n" + encoded
    )
    invite = Mailbox.parse_invite(source)
    assert invite is not None
    assert invite.uid == "xyz-9@aethercal"
    assert invite.is_confirmation is False


def test_the_ics_parser_unfolds_long_lines() -> None:
    """RFC 5545 folds long lines; a reader that does not unfold sees a truncated uid."""
    ics = (
        "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
        "UID:very-long-identifier-that-was\r\n folded-across-two-lines@aethercal\r\n"
        "STATUS:CONFIRMED\r\nSEQUENCE:0\r\nEND:VEVENT\r\nEND:VCALENDAR"
    )
    source = "MIME-Version: 1.0\r\nContent-Type: text/calendar\r\n\r\n" + ics
    invite = Mailbox.parse_invite(source)
    assert invite is not None
    assert invite.uid == "very-long-identifier-thatwasfolded-across-two-lines@aethercal".replace(
        "thatwas", "that-was"
    )


def test_a_message_without_a_calendar_part_yields_no_invite() -> None:
    source = "MIME-Version: 1.0\r\nContent-Type: text/plain\r\n\r\njust a reminder"
    assert Mailbox.parse_invite(source) is None


# --------------------------------------------------------------------------------------
# ==The same shape as S16, one instrument over, and nobody reported it.==
#
# `sink_events_for_booking` matched with `booking_id.encode() not in raw` -- a substring scan of the
# delivery's BYTES. A booking's id appears in more places than its own identity field: inside a
# cancel/reschedule URL, inside a description, or as the predecessor a successor was rescheduled
# from. Two bookings' events could both be attributed to one of them, and C7's oracle is a COUNT.
# --------------------------------------------------------------------------------------


def test_identifier_values_collects_only_identifier_shaped_keys() -> None:
    payload = {
        "event": "booking.cancelled",
        "data": {"id": "book-1", "event_type_id": "et-9", "guest_email": "g@x.test"},
    }
    assert identifier_values(payload) == {"book-1", "et-9"}


def test_identifier_values_reaches_into_nested_lists() -> None:
    payload = {"items": [{"id": "a"}, {"nested": {"booking_id": "b"}}]}
    assert identifier_values(payload) == {"a", "b"}


def test_an_id_that_appears_only_in_a_URL_is_NOT_an_identity_match() -> None:
    """==The defect, as the payload that used to be counted twice.==

    This delivery is about booking `succ-2`; it merely MENTIONS `book-1` in the cancel link and as
    the predecessor it replaced. The raw-bytes scan attributed it to `book-1` as well.
    """
    payload = {
        "event": "booking.created",
        "data": {
            "id": "succ-2",
            "cancel_url": "https://book.example/cancel/book-1",
            "description": "rescheduled from book-1",
        },
    }
    ids = identifier_values(payload)
    assert "succ-2" in ids
    assert "book-1" not in ids
    assert "book-1" in json.dumps(payload), "the id IS in the bytes -- that is the point"


def test_sink_events_for_booking_does_not_cross_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The CALL SITE, pinned -- the helper's own test could not reach it.==

    Mutating `sink_events_for_booking` back to the raw-bytes scan left the `identifier_values` tests
    green, because they exercise the helper and never the code that uses it. Fifth time this branch
    has been taught that a pure test binds the judgement and not the wiring, so this one drives the
    real function over a stubbed sink.

    `succ-2`'s delivery merely MENTIONS `book-1` in a URL. Attributing it to `book-1` would give
    that booking two `booking.created` events, and C7's oracle is a count.
    """
    captured = [
        {
            "body_b64": base64.b64encode(
                json.dumps({"event": "booking.created", "data": {"id": "book-1"}}).encode()
            ).decode()
        },
        {
            "body_b64": base64.b64encode(
                json.dumps(
                    {
                        "event": "booking.created",
                        "data": {
                            "id": "succ-2",
                            "cancel_url": "https://book.example/cancel/book-1",
                        },
                    }
                ).encode()
            ).decode()
        },
    ]
    monkeypatch.setattr("aethercal_sim.scenarios._captured", lambda _url: captured)
    counts, unreadable = sink_events_for_booking("http://localhost:9099", "book-1")
    assert unreadable == 0
    assert counts == {"booking.created": 1}, "the successor's delivery is not book-1's"
