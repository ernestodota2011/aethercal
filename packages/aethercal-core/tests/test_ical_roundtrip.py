"""Structural round-trip tests for the iCalendar (.ics) import/export module (RF-05).

The invariant under test is ``event_from_ics(event_to_ics(ev)) == ev`` — a
field-for-field (Pydantic value-equality) round-trip of the supported ``Event``
surface. Recurrence *expansion* equivalence is intentionally out of scope here
(that belongs to the recurrence engine); we only assert structural fidelity.
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aethercal.core.ical import event_from_ics, event_to_ics
from aethercal.core.model import Event


def test_roundtrip_simple_non_recurring_event() -> None:
    ev = Event(
        dtstart=datetime(2026, 3, 1, 9, 0),
        duration=timedelta(minutes=30),
        timezone="America/Santiago",
    )
    assert event_from_ics(event_to_ics(ev)) == ev


def test_roundtrip_weekly_recurring_event() -> None:
    ev = Event(
        dtstart=datetime(2026, 3, 2, 14, 0),
        duration=timedelta(hours=1),
        timezone="America/Santiago",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    )
    assert event_from_ics(event_to_ics(ev)) == ev


@pytest.mark.parametrize(
    "zone",
    [
        "America/Santiago",  # DST-observing, southern hemisphere
        "Australia/Lord_Howe",  # rare 30-minute (10:30/11:00) offset
        "Pacific/Kiritimati",  # extreme +14:00 offset
        "UTC",  # emitted in the Z form (no TZID)
    ],
)
def test_roundtrip_across_tricky_timezones(zone: str) -> None:
    ev = Event(
        dtstart=datetime(2026, 6, 21, 8, 30),
        duration=timedelta(minutes=90),
        timezone=zone,
        rrule="FREQ=WEEKLY;BYDAY=SU",
        exdates=frozenset({datetime(2026, 6, 28, 8, 30)}),
    )
    assert event_from_ics(event_to_ics(ev)) == ev


def test_roundtrip_event_with_exdates_and_rdates() -> None:
    ev = Event(
        dtstart=datetime(2026, 3, 2, 9, 0),
        duration=timedelta(minutes=45),
        timezone="America/Santiago",
        rrule="FREQ=DAILY;COUNT=10",
        exdates=frozenset({datetime(2026, 3, 4, 9, 0), datetime(2026, 3, 6, 9, 0)}),
        rdates=frozenset({datetime(2026, 3, 20, 9, 0), datetime(2026, 3, 21, 9, 0)}),
    )
    assert event_from_ics(event_to_ics(ev)) == ev


# --- Property-based round-trip ------------------------------------------------

# A curated list of real IANA zones, deliberately spanning the awkward cases:
# UTC (emitted as the Z form), DST-observing zones, 30/45-minute offsets, and the
# +12:45 / +14:00 extremes. Aliases (e.g. "Etc/UTC") are excluded on purpose —
# they canonicalize to "UTC" on the way back and would break exact equality.
_ZONES = [
    "UTC",
    "America/Santiago",
    "America/New_York",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Berlin",
    "Africa/Nairobi",
    "Asia/Kolkata",  # +05:30
    "Asia/Kathmandu",  # +05:45
    "Australia/Lord_Howe",  # +10:30 / +11:00
    "Pacific/Chatham",  # +12:45 / +13:45
    "Pacific/Kiritimati",  # +14:00
]

# RRULE bodies verified stable through icalendar's canonical part ordering: each
# satisfies ``vRecur.from_ical(r).to_ical() == r`` so exact-equality round-trip
# holds. RRULEs whose parts get reordered (e.g. BYMONTH before BYMONTHDAY, or a
# mid-rule WKST) are intentionally omitted — the model stores rrule verbatim, so
# only canonically-ordered rrules survive a byte-exact round-trip (see the
# ``aethercal.core.ical`` module docstring). ``None`` means "no recurrence".
_RRULES = [
    None,
    "FREQ=DAILY",
    "FREQ=DAILY;COUNT=10",
    "FREQ=WEEKLY;BYDAY=MO,WE,FR",
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU",
    "FREQ=MONTHLY;BYMONTHDAY=15",
    "FREQ=MONTHLY;BYDAY=1MO",
    "FREQ=DAILY;UNTIL=20261231T000000Z",
]

# Whole-second wall-time datetimes: RFC 5545 DATE-TIME has no sub-second field,
# so microseconds are dropped by the format and must be excluded from the domain
# (the property test above witnessed this boundary before it was curated out).
_naive_dt = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2050, 12, 31, 23, 59, 59),
).map(lambda moment: moment.replace(microsecond=0))

# Whole-second, strictly-positive durations (1 second .. 14 days) — sub-second
# DURATION is likewise not representable in RFC 5545.
_durations = st.integers(min_value=1, max_value=60 * 60 * 24 * 14).map(
    lambda seconds: timedelta(seconds=seconds)
)

_events = st.builds(
    Event,
    dtstart=_naive_dt,
    duration=_durations,
    timezone=st.sampled_from(_ZONES),
    rrule=st.sampled_from(_RRULES),
    exdates=st.frozensets(_naive_dt, max_size=4),
    rdates=st.frozensets(_naive_dt, max_size=4),
)


@settings(max_examples=300, deadline=None)
@given(ev=_events)
def test_property_structural_roundtrip(ev: Event) -> None:
    assert event_from_ics(event_to_ics(ev)) == ev
