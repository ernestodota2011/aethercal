"""Property-based proof of the slots engine (F0-07) — this is where booking correctness is won.

Strategies CONSTRUCT valid inputs directly (disjoint availability, arbitrary busy blocks, valid
event types, a random `now`) so no example is wasted on filtering. Over thousands of random cases
we assert the five load-bearing invariants, the first of which — no offered (buffered) slot ever
overlaps a busy block — is the anti-double-booking guarantee the whole system rests on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aethercal.core.model import Buffer, EventType, TimeInterval
from aethercal.core.slots import available_slots

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


@st.composite
def disjoint_availability(draw: st.DrawFn) -> list[TimeInterval]:
    """A sorted list of pairwise-disjoint aware intervals (so each slot fits exactly one)."""
    count = draw(st.integers(min_value=0, max_value=5))
    intervals: list[TimeInterval] = []
    cursor = draw(st.integers(min_value=-500, max_value=500))
    for _ in range(count):
        cursor += draw(st.integers(min_value=0, max_value=240))  # gap before this interval
        length = draw(st.integers(min_value=1, max_value=600))
        intervals.append(TimeInterval(start=_at(cursor), end=_at(cursor + length)))
        cursor += length
    return intervals


@st.composite
def busy_blocks(draw: st.DrawFn) -> list[TimeInterval]:
    """Arbitrary aware busy intervals (may overlap each other; that is irrelevant to the guard)."""
    count = draw(st.integers(min_value=0, max_value=6))
    blocks: list[TimeInterval] = []
    for _ in range(count):
        start = draw(st.integers(min_value=-700, max_value=3500))
        length = draw(st.integers(min_value=1, max_value=600))
        blocks.append(TimeInterval(start=_at(start), end=_at(start + length)))
    return blocks


@st.composite
def event_types(draw: st.DrawFn) -> EventType:
    duration = timedelta(minutes=draw(st.integers(min_value=5, max_value=120)))
    before = timedelta(minutes=draw(st.integers(min_value=0, max_value=60)))
    after = timedelta(minutes=draw(st.integers(min_value=0, max_value=60)))
    increment: timedelta | None = None
    if draw(st.booleans()):
        increment = timedelta(minutes=draw(st.integers(min_value=5, max_value=120)))
    min_notice = timedelta(minutes=draw(st.integers(min_value=0, max_value=600)))
    max_advance = timedelta(minutes=draw(st.integers(min_value=1, max_value=100_000)))
    return EventType(
        duration=duration,
        buffer=Buffer(before=before, after=after),
        increment=increment,
        min_notice=min_notice,
        max_advance=max_advance,
    )


@given(
    available=disjoint_availability(),
    busy=busy_blocks(),
    event_type=event_types(),
    now_minute=st.integers(min_value=-2000, max_value=4000),
)
@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_slots_invariants(
    available: list[TimeInterval],
    busy: list[TimeInterval],
    event_type: EventType,
    now_minute: int,
) -> None:
    now = _at(now_minute)
    slots = available_slots(available, busy, event_type, now=now)

    earliest = now + event_type.min_notice
    latest = now + event_type.max_advance
    before = event_type.buffer.before
    after = event_type.buffer.after

    # (5a) Output is sorted by start.
    assert [s.start for s in slots] == sorted(s.start for s in slots)

    for slot in slots:
        # (2) Every slot is exactly `duration` long.
        assert slot.duration == event_type.duration
        # (4) Every start is inside the booking window (inclusive bounds).
        assert earliest <= slot.start <= latest
        # (3) Every slot lies entirely within some available interval.
        assert any(iv.start <= slot.start and slot.end <= iv.end for iv in available)
        # (1) THE anti-double-booking invariant: the buffered span hits no busy block.
        buffered = TimeInterval(start=slot.start - before, end=slot.end + after)
        assert not any(buffered.overlaps(b) for b in busy)

    # (5b) Determinism: identical inputs -> identical output.
    assert available_slots(available, busy, event_type, now=now) == slots
