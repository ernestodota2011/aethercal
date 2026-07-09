"""Conflict / overlap detection over half-open intervals (RF-04).

Two requests must never both take the same slot; the pure detection logic that a
booking service calls before confirming lives in ``aethercal.core.conflicts``.
"""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aethercal.core.conflicts import (
    SlotConflictError,
    busy_from_bookings,
    find_overlapping_pairs,
    has_conflict,
    validate_no_conflict,
)
from aethercal.core.model import Booking, BookingStatus, TimeInterval


def _iv(h1: int, h2: int) -> TimeInterval:
    """Build a same-day UTC interval [h1:00, h2:00)."""
    return TimeInterval(
        start=datetime(2026, 3, 1, h1, 0, tzinfo=UTC),
        end=datetime(2026, 3, 1, h2, 0, tzinfo=UTC),
    )


def test_touching_intervals_do_not_conflict() -> None:
    # [9,10) and [10,11) share only the endpoint -> half-open, NO conflict.
    assert has_conflict(_iv(9, 10), [_iv(10, 11)]) is False


def test_overlapping_interval_conflicts() -> None:
    # [9,10) vs [9:30,10:30) genuinely overlap.
    overlapping = TimeInterval(
        start=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
        end=datetime(2026, 3, 1, 10, 30, tzinfo=UTC),
    )
    assert has_conflict(_iv(9, 10), [overlapping]) is True


def test_disjoint_intervals_do_not_conflict() -> None:
    assert has_conflict(_iv(9, 10), [_iv(11, 12)]) is False


def test_no_conflict_against_empty_busy() -> None:
    assert has_conflict(_iv(9, 10), []) is False


def test_conflict_when_any_busy_overlaps() -> None:
    # Disjoint, disjoint, then one that overlaps -> conflict.
    busy = [_iv(7, 8), _iv(11, 12), _iv(9, 11)]
    assert has_conflict(_iv(10, 10 + 1), busy) is True


# --- busy_from_bookings -------------------------------------------------------


def test_busy_from_bookings_excludes_cancelled() -> None:
    confirmed = Booking(interval=_iv(9, 10))  # defaults to CONFIRMED
    pending = Booking(interval=_iv(11, 12), status=BookingStatus.PENDING)
    cancelled = Booking(interval=_iv(13, 14), status=BookingStatus.CANCELLED)

    busy = busy_from_bookings([confirmed, pending, cancelled])

    assert busy == [_iv(9, 10), _iv(11, 12)]  # cancelled dropped, order preserved


def test_no_conflict_against_a_cancelled_booking() -> None:
    cancelled = Booking(interval=_iv(9, 11), status=BookingStatus.CANCELLED)
    # Candidate overlaps the cancelled slot, but it no longer occupies -> no conflict.
    assert has_conflict(_iv(10, 11), busy_from_bookings([cancelled])) is False


def test_conflict_against_a_pending_booking() -> None:
    pending = Booking(interval=_iv(9, 11), status=BookingStatus.PENDING)
    assert has_conflict(_iv(10, 11), busy_from_bookings([pending])) is True


# --- validate_no_conflict (RF-04 guard) ---------------------------------------


def test_validate_no_conflict_passes_when_slot_free() -> None:
    existing = [Booking(interval=_iv(9, 10)), Booking(interval=_iv(11, 12))]
    # [10,11) sits between the two booked slots, touching both -> allowed.
    assert validate_no_conflict(_iv(10, 11), existing) is None


def test_validate_no_conflict_raises_on_collision() -> None:
    existing = [Booking(interval=_iv(9, 11))]
    with pytest.raises(SlotConflictError) as excinfo:
        validate_no_conflict(_iv(10, 12), existing)
    # The error carries the candidate and the specific booked interval it collided with.
    assert excinfo.value.candidate == _iv(10, 12)
    assert excinfo.value.conflict == _iv(9, 11)


def test_validate_no_conflict_ignores_cancelled_bookings() -> None:
    cancelled = Booking(interval=_iv(9, 11), status=BookingStatus.CANCELLED)
    assert validate_no_conflict(_iv(10, 11), [cancelled]) is None


def test_validate_no_conflict_reports_first_colliding_booking() -> None:
    # Two occupying bookings both overlap; the guard names the first one in input order.
    existing = [Booking(interval=_iv(8, 12)), Booking(interval=_iv(10, 13))]
    with pytest.raises(SlotConflictError) as excinfo:
        validate_no_conflict(_iv(11, 12), existing)
    assert excinfo.value.conflict == _iv(8, 12)


# --- find_overlapping_pairs ---------------------------------------------------


def test_find_overlapping_pairs_empty_input() -> None:
    assert find_overlapping_pairs([]) == []


def test_find_overlapping_pairs_all_disjoint() -> None:
    assert find_overlapping_pairs([_iv(9, 10), _iv(11, 12), _iv(13, 14)]) == []


def test_find_overlapping_pairs_touching_chain_has_no_pairs() -> None:
    # Back-to-back slots touch at endpoints only -> half-open, no double-booking.
    assert find_overlapping_pairs([_iv(9, 10), _iv(10, 11), _iv(11, 12)]) == []


def test_find_overlapping_pairs_single_pair() -> None:
    assert find_overlapping_pairs([_iv(9, 11), _iv(10, 12)]) == [(0, 1)]


def test_find_overlapping_pairs_reports_original_indices() -> None:
    # index 0 disjoint; indices 1 and 2 overlap -> pair is (1, 2), not (0, 1).
    assert find_overlapping_pairs([_iv(6, 7), _iv(9, 11), _iv(10, 12)]) == [(1, 2)]


def test_find_overlapping_pairs_three_mutually_overlapping() -> None:
    # All three straddle 10:00 -> every pair overlaps.
    assert find_overlapping_pairs([_iv(8, 11), _iv(9, 12), _iv(10, 13)]) == [
        (0, 1),
        (0, 2),
        (1, 2),
    ]


def test_find_overlapping_pairs_nested_interval_overlaps_container() -> None:
    # [9,17) fully contains [12,13) -> they overlap.
    big = TimeInterval(
        start=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        end=datetime(2026, 3, 1, 17, 0, tzinfo=UTC),
    )
    assert find_overlapping_pairs([big, _iv(12, 13)]) == [(0, 1)]


def test_find_overlapping_pairs_chained_overlap_is_not_transitive() -> None:
    # [9,11) & [10,12) overlap; [10,12) & [11,13) overlap; but [9,11) & [11,13) only
    # touch at 11:00 -> half-open, so (0, 2) is NOT a pair.
    assert find_overlapping_pairs([_iv(9, 11), _iv(10, 12), _iv(11, 13)]) == [
        (0, 1),
        (1, 2),
    ]


def test_find_overlapping_pairs_unsorted_input_still_canonical() -> None:
    # Later-starting interval given first; result pairs are (min, max) index, sorted.
    assert find_overlapping_pairs([_iv(10, 12), _iv(9, 11)]) == [(0, 1)]


# --- property tests (Hypothesis) ----------------------------------------------
#
# The invariants are cross-checked against a brute-force oracle that trusts only
# TimeInterval.overlaps (the frozen, separately-tested primitive), so a bug in our
# detection code cannot hide behind the same bug in the oracle.


@st.composite
def _valid_intervals(draw: st.DrawFn) -> TimeInterval:
    """A valid TimeInterval: an aware start plus a strictly-positive duration.

    Constructed (never filtered) so Hypothesis wastes no examples and the pydantic
    end-after-start invariant always holds.
    """
    start = draw(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 1, 1),
            timezones=st.just(UTC),
        )
    )
    minutes = draw(st.integers(min_value=1, max_value=7 * 24 * 60))
    return TimeInterval(start=start, end=start + timedelta(minutes=minutes))


@settings(max_examples=300, deadline=None)
@given(candidate=_valid_intervals(), busy=st.lists(_valid_intervals(), max_size=12))
def test_has_conflict_iff_overlaps_some_busy(
    candidate: TimeInterval, busy: list[TimeInterval]
) -> None:
    expected = any(candidate.overlaps(b) for b in busy)
    assert has_conflict(candidate, busy) is expected


@settings(max_examples=300, deadline=None)
@given(intervals=st.lists(_valid_intervals(), max_size=15))
def test_find_overlapping_pairs_iff_intervals_overlap(intervals: list[TimeInterval]) -> None:
    expected = sorted(
        (i, j)
        for i in range(len(intervals))
        for j in range(i + 1, len(intervals))
        if intervals[i].overlaps(intervals[j])
    )
    assert find_overlapping_pairs(intervals) == expected
