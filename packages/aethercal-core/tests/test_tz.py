"""Timezone layer: wall-time <-> instant conversion with DST gap/fold handling.

Golden offsets were verified against the live IANA database before being encoded here."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import pytest

from aethercal.core.tz import is_ambiguous, is_imaginary, localize, to_instant


def test_localize_rejects_aware_input() -> None:
    with pytest.raises(ValueError, match="naive"):
        localize(datetime(2026, 1, 15, 12, 0, tzinfo=UTC), "America/New_York")


def test_localize_rejects_unknown_timezone() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        localize(datetime(2026, 1, 1, 12, 0), "Mars/Phobos")


def test_localize_applies_standard_and_dst_offsets() -> None:
    assert localize(datetime(2026, 1, 15, 12, 0), "America/New_York").utcoffset() == timedelta(
        hours=-5
    )
    assert localize(datetime(2026, 7, 15, 12, 0), "America/New_York").utcoffset() == timedelta(
        hours=-4
    )


def test_to_instant_converts_walltime_to_utc() -> None:
    assert to_instant(datetime(2026, 1, 15, 12, 0), "America/New_York") == datetime(
        2026, 1, 15, 17, 0, tzinfo=UTC
    )


def test_fall_back_time_is_ambiguous_and_fold_selects_instant() -> None:
    w = datetime(2026, 11, 1, 1, 30)  # occurs twice in New York (fall back)
    assert is_ambiguous(w, "America/New_York") is True
    earlier = to_instant(w, "America/New_York", fold=0)
    later = to_instant(w, "America/New_York", fold=1)
    assert later - earlier == timedelta(hours=1)


def test_spring_forward_time_is_imaginary() -> None:
    # 02:30 on 2026-03-08 does not exist in New York (spring forward 02:00 -> 03:00)
    assert is_imaginary(datetime(2026, 3, 8, 2, 30), "America/New_York") is True


def test_normal_time_is_neither_ambiguous_nor_imaginary() -> None:
    w = datetime(2026, 6, 1, 12, 0)
    assert is_ambiguous(w, "America/New_York") is False
    assert is_imaginary(w, "America/New_York") is False


def test_half_hour_dst_zone_lord_howe() -> None:
    assert localize(datetime(2026, 6, 1, 12, 0), "Australia/Lord_Howe").utcoffset() == timedelta(
        hours=10, minutes=30
    )
    assert localize(datetime(2026, 12, 1, 12, 0), "Australia/Lord_Howe").utcoffset() == timedelta(
        hours=11
    )


def test_southern_hemisphere_santiago_offsets() -> None:
    assert localize(datetime(2026, 1, 15, 12, 0), "America/Santiago").utcoffset() == timedelta(
        hours=-3
    )
    assert localize(datetime(2026, 7, 15, 12, 0), "America/Santiago").utcoffset() == timedelta(
        hours=-4
    )
