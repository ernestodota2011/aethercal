"""Golden integration tests: real .ics files on disk -> parse -> recurrence expansion (F0-04).

These exercise the seam between the two shipped modules -- ``aethercal.core.ical`` (import) and
``aethercal.core.recurrence`` (expansion) -- against .ics *files* rather than in-memory Events,
catching wire-format issues (line folding, CRLF line endings, TZID parameters, whole-VCALENDAR
framing, ignored X-props like SUMMARY/DESCRIPTION/UID) that construction-based unit tests miss.

Scope: the fixtures live within AetherCal's documented import surface -- a single VEVENT carrying
DTSTART (``TZID=<IANA>`` or the UTC ``Z`` form), DURATION (not DTEND) and RFC 5545
RRULE/EXDATE/RDATE. Broader real-world imports (DTEND, ``VALUE=DATE`` all-day events, an embedded
VTIMEZONE, multiple VEVENTs) are intentionally out of scope for the core importer and land with the
Google-sync work in F1. This file documents that boundary rather than hiding it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from aethercal.core.ical import event_from_ics
from aethercal.core.model import Occurrence, TimeInterval
from aethercal.core.recurrence import expand

_FIXTURES = Path(__file__).parent / "fixtures"


def _utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeInterval:
    return TimeInterval(start=start, end=end)


def _load_ics(name: str) -> str:
    """Read a fixture and reconstruct RFC 5545 CRLF line endings at read time.

    Fixtures are stored LF (the repo ``.gitattributes`` normalizes text to LF); real .ics on the
    wire is CRLF, so we rebuild it here to exercise the parser against realistic bytes regardless
    of how git checked the file out.
    """
    text = (_FIXTURES / name).read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _starts(occs: list[Occurrence]) -> list[datetime]:
    return [o.start for o in occs]


def test_golden_weekly_standup_ny_crosses_dst_and_honours_exdate() -> None:
    ev = event_from_ics(_load_ics("team_standup_ny.ics"))
    assert ev.timezone == "America/New_York"
    assert ev.duration == timedelta(minutes=15)
    # icalendar canonicalizes RRULE part order on parse (FREQ;BYDAY;COUNT -> FREQ;COUNT;BYDAY);
    # the recurrence is identical, so we assert behavior via the expansion below, not the text.
    assert ev.rrule == "FREQ=WEEKLY;COUNT=4;BYDAY=MO"

    occs = expand(ev, _window(_utc(2026, 3, 1), _utc(2026, 3, 31)))

    # 09:30 local every Monday for four weeks, minus the Mar-16 EXDATE. The wall clock stays 09:30
    # while the absolute instant shifts 14:30Z -> 13:30Z across the 2026-03-08 spring-forward.
    assert _starts(occs) == [
        _utc(2026, 3, 2, 14, 30),  # EST (-5)
        _utc(2026, 3, 9, 13, 30),  # EDT (-4)
        _utc(2026, 3, 23, 13, 30),  # Mar-16 removed by EXDATE
    ]
    assert all(o.end - o.start == timedelta(minutes=15) for o in occs)


def test_golden_daily_office_hours_utc_with_rdate() -> None:
    ev = event_from_ics(_load_ics("office_hours_utc.ics"))
    assert ev.timezone == "UTC"
    assert ev.duration == timedelta(minutes=45)

    occs = expand(ev, _window(_utc(2026, 5, 1), _utc(2026, 6, 1)))

    # Three daily occurrences plus the one-off RDATE on May-20.
    assert _starts(occs) == [
        _utc(2026, 5, 10, 8, 0),
        _utc(2026, 5, 11, 8, 0),
        _utc(2026, 5, 12, 8, 0),
        _utc(2026, 5, 20, 15, 0),
    ]
