"""Wall-time <-> instant conversion with explicit DST gap/fold handling.

A "wall time" is a naive datetime interpreted in a given IANA zone. Because of DST some wall
times are *ambiguous* (they occur twice, at a fall-back) and some are *imaginary* (they never
occur, at a spring-forward). These helpers make that explicit so the recurrence and slot engines
never silently produce a wrong instant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def _require_naive(walltime: datetime) -> None:
    if walltime.tzinfo is not None:
        raise ValueError("wall time must be naive (the timezone is passed separately)")


def localize(walltime: datetime, tz: str, *, fold: int = 0) -> datetime:
    """Attach the IANA ``tz`` to a naive ``walltime``.

    ``fold`` selects which instant an ambiguous wall time maps to (0 = the earlier, first
    occurrence; 1 = the later one), per PEP 495.
    """
    _require_naive(walltime)
    return walltime.replace(tzinfo=ZoneInfo(tz), fold=fold)


def to_instant(walltime: datetime, tz: str, *, fold: int = 0) -> datetime:
    """Convert a naive ``walltime`` in ``tz`` to its absolute UTC instant."""
    return localize(walltime, tz, fold=fold).astimezone(UTC)


def is_ambiguous(walltime: datetime, tz: str) -> bool:
    """True if ``walltime`` occurs twice in ``tz`` (a DST fall-back)."""
    _require_naive(walltime)
    zone = ZoneInfo(tz)
    return (
        walltime.replace(tzinfo=zone, fold=0).utcoffset()
        != walltime.replace(tzinfo=zone, fold=1).utcoffset()
    )


def is_imaginary(walltime: datetime, tz: str) -> bool:
    """True if ``walltime`` never occurs in ``tz`` (a DST spring-forward gap)."""
    _require_naive(walltime)
    zone = ZoneInfo(tz)
    aware = walltime.replace(tzinfo=zone)
    roundtrip = aware.astimezone(UTC).astimezone(zone)
    return roundtrip.replace(tzinfo=None) != walltime
