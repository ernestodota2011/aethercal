"""The harness's own unit tests: the pure logic every reported number is computed with.

==A measuring instrument needs its own calibration.== The controls inside a run prove the harness
can see the product misbehave; these prove the arithmetic underneath the report is right, offline
and in milliseconds. A p95 computed wrongly is not a smaller problem than a race counted wrongly —
it is the same problem one layer down, and it would be invisible in a run whose every control
passed.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from aethercal_sim.__main__ import measure_confirmations
from aethercal_sim.client import Response, extract_error_code
from aethercal_sim.measure import (
    ErrorTally,
    Latency,
    Mailbox,
    MailboxRead,
    MailMessage,
    percentile,
)
from aethercal_sim.report import REQUIRED_CONTROL_IDS, missing_controls, verdict_for
from aethercal_sim.scenarios import (
    BookedRef,
    CancelWebhookObservation,
    ConfirmationCoverage,
    Control,
    DeadmanObservation,
    DiaryRead,
    OrganicResult,
    judge_cancel_idempotency,
    judge_closed_day,
    judge_confirmation_coverage,
    judge_drain_deadman,
    judge_lineage,
    judge_organic_accounting,
    next_saturday,
    pick_micro_slot,
    read_offer,
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
    control = judge_lineage(broken, ident="C9", guards="g", at_most=at_most)
    assert control.ran is True
    assert control.passed is False
    assert "could not be read completely" in control.observed


def test_lineage_passes_on_a_complete_read_with_one_survivor() -> None:
    read = DiaryRead(["abc confirmed"], complete=True)
    assert judge_lineage(read, ident="C8", guards="g", at_most=False).passed
    assert judge_lineage(read, ident="C9", guards="g", at_most=True).passed


def test_lineage_fails_when_two_live_appointments_survive() -> None:
    """The defect the pair exists to catch, on a read that IS trustworthy."""
    read = DiaryRead(["a confirmed", "b confirmed"], complete=True)
    assert not judge_lineage(read, ident="C9", guards="g", at_most=True).passed


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
    read = DiaryRead([], complete=True)
    assert judge_lineage(read, ident="C9", guards="g", at_most=True).passed
    assert not judge_lineage(read, ident="C8", guards="g", at_most=False).passed


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


def _cancel(counts: dict[str, int], *, drained: bool = True) -> CancelWebhookObservation:
    return CancelWebhookObservation(
        counts=counts,
        drained=drained,
        drain_wait_seconds=3.0,
        appeared_after_seconds=1.0 if counts.get("booking.cancelled") else None,
        appear_timeout_seconds=60.0,
        settle_seconds=10.0,
    )


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


class _StubMailbox:
    """A mailbox that hands back one prepared read, and counts how many times it was asked."""

    def __init__(self, read: MailboxRead) -> None:
        self.read = read
        self.reads = 0

    def read_all(self) -> MailboxRead:
        self.reads += 1
        return self.read


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
        [MailMessage("g1@guests.sim.test", "Confirmed", sent + timedelta(milliseconds=50))],
        True,
        1,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(_StubMailbox(read), booked)  # type: ignore[arg-type]
    assert coverage.matched == 1
    assert coverage.negative_deltas == 0
    assert latency.count == 1
    assert latency.samples[0] == pytest.approx(50.0, abs=1.0)


def test_a_genuinely_negative_delta_is_counted_and_voids_rather_than_vanishing() -> None:
    """Against the send instant this cannot be legitimate: skew, reported rather than filtered."""
    sent = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    booked = [BookedRef("id1", "b", "g1@guests.sim.test", "s", sent.timestamp())]
    read = MailboxRead(
        [MailMessage("g1@guests.sim.test", "Confirmed", sent - timedelta(seconds=2))],
        True,
        1,
        1,
        500,
    )
    latency, _out, coverage = measure_confirmations(_StubMailbox(read), booked)  # type: ignore[arg-type]
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
    )
    assert out.complete is False
    assert stub.reads == 1
    assert coverage.attempts == 1
    assert not judge_confirmation_coverage(coverage).passed
