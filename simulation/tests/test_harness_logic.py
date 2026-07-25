"""The harness's own unit tests: the pure logic every reported number is computed with.

==A measuring instrument needs its own calibration.== The controls inside a run prove the harness
can see the product misbehave; these prove the arithmetic underneath the report is right, offline
and in milliseconds. A p95 computed wrongly is not a smaller problem than a race counted wrongly —
it is the same problem one layer down, and it would be invisible in a run whose every control
passed.
"""

from __future__ import annotations

from datetime import date

import pytest

from aethercal_sim.client import Response, extract_error_code
from aethercal_sim.measure import ErrorTally, Latency, percentile
from aethercal_sim.report import REQUIRED_CONTROL_IDS, missing_controls, verdict_for
from aethercal_sim.scenarios import Control, next_saturday
from aethercal_sim.traffic import WEEKDAY_WEIGHTS, PlannedBooking, plan_two_weeks, summarise_plan

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
