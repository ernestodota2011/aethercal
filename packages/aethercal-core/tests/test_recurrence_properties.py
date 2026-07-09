"""Property-based tests for recurrence expansion.

Two layers: (1) internal invariants across thousands of random events/windows, and (2) a
differential cross-check of the localized expansion against an independent oracle
(``recurring-ical-events``). DST-pathological instants (occurrences landing exactly in a
spring-forward gap or a fall-back fold) are excluded from the differential check because they are
legitimately implementation-defined; the golden example tests pin the across-transition behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
from dateutil.rrule import rrulestr
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from icalendar import Calendar
from icalendar import Event as ICalEvent
from icalendar.prop import vRecur

from aethercal.core.model import Event, TimeInterval
from aethercal.core.recurrence import expand
from aethercal.core.tz import is_ambiguous, is_imaginary

TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Santiago",
    "Europe/Madrid",
    "Australia/Lord_Howe",
    "Pacific/Kiritimati",
    "Asia/Kolkata",
]
FREQS = ["DAILY", "WEEKLY", "MONTHLY"]


@st.composite
def events(draw: st.DrawFn) -> Event:
    tz = draw(st.sampled_from(TIMEZONES))
    dt = draw(
        st.datetimes(min_value=datetime(2024, 1, 1), max_value=datetime(2027, 12, 31))
    ).replace(microsecond=0)
    duration = timedelta(minutes=draw(st.integers(min_value=5, max_value=240)))
    freq = draw(st.sampled_from(FREQS))
    count = draw(st.integers(min_value=1, max_value=8))
    interval = draw(st.integers(min_value=1, max_value=3))
    rrule = f"FREQ={freq};INTERVAL={interval};COUNT={count}"
    return Event(dtstart=dt, duration=duration, timezone=tz, rrule=rrule)


@given(
    event=events(),
    win_start=st.datetimes(min_value=datetime(2023, 1, 1), max_value=datetime(2030, 1, 1)),
    win_hours=st.integers(min_value=1, max_value=60 * 24),
)
@settings(max_examples=10_000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_expansion_invariants(event: Event, win_start: datetime, win_hours: int) -> None:
    start = win_start.replace(tzinfo=UTC)
    window = TimeInterval(start=start, end=start + timedelta(hours=win_hours))
    occs = expand(event, window)
    starts = [o.start for o in occs]
    # Sorted, deduplicated, every returned occurrence really overlaps the window,
    # and never more than the recurrence COUNT.
    assert starts == sorted(starts)
    assert len(starts) == len(set(starts))
    assert all(o.interval.overlaps(window) for o in occs)
    count = int(event.rrule.split("COUNT=")[1]) if event.rrule else 1
    assert len(occs) <= count


def _oracle_starts(event: Event, window: TimeInterval) -> set[datetime]:
    cal = Calendar()
    cal.add("prodid", "-//aethercal-test//")
    cal.add("version", "2.0")
    iev = ICalEvent()
    iev.add("uid", "prop@test")
    iev.add("dtstart", event.dtstart.replace(tzinfo=ZoneInfo(event.timezone)))
    iev.add("duration", event.duration)
    if event.rrule is not None:
        iev.add("rrule", vRecur.from_ical(event.rrule))
    cal.add_component(iev)
    resolved = recurring_ical_events.of(cal).between(window.start, window.end)
    return {comp["DTSTART"].dt.astimezone(UTC) for comp in resolved}


@given(event=events())
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_expansion_matches_recurring_ical_events_oracle(event: Event) -> None:
    # Skip examples where any occurrence lands in a DST gap/fold (implementation-defined).
    for wall in rrulestr(event.rrule, dtstart=event.dtstart):  # type: ignore[arg-type]
        assume(not is_imaginary(wall, event.timezone))
        assume(not is_ambiguous(wall, event.timezone))
    # A window wide enough to contain the whole (COUNT-bounded) recurrence set.
    window = TimeInterval(
        start=datetime(2022, 1, 1, tzinfo=UTC),
        end=datetime(2031, 1, 1, tzinfo=UTC),
    )
    mine = {o.start for o in expand(event, window)}
    assert mine == _oracle_starts(event, window)
