"""Pure transforms for the Google Calendar spike (F0-11): no network, fully testable.

Google API responses are plain dicts. These helpers turn a ``freebusy().query()`` response into
``aethercal-core`` ``TimeInterval``s and build an ``events().insert()`` body that requests a Google
Meet link. Keeping them pure lets us TDD the mapping logic without touching the live API -- the live
call is the spike demo itself. The untyped google-api-python-client stays behind the ``Any`` seam in
``calendar.py``; everything here is our own strictly-typed code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aethercal.core.model import TimeInterval


def parse_freebusy(response: dict[str, Any], calendar_id: str) -> list[TimeInterval]:
    """Map a calendar's busy blocks from a freebusy response into sorted UTC ``TimeInterval``s.

    Raises if Google reported per-calendar errors (e.g. the calendar is not accessible) rather than
    silently returning an empty (and dangerously "all-free") result.
    """
    calendars: dict[str, Any] = response.get("calendars", {})
    entry: dict[str, Any] = calendars.get(calendar_id, {})
    errors: Any = entry.get("errors")
    if errors:
        raise RuntimeError(f"freebusy reported errors for calendar {calendar_id!r}: {errors}")

    intervals: list[TimeInterval] = []
    for block in entry.get("busy", []):
        intervals.append(TimeInterval(start=_rfc3339(block["start"]), end=_rfc3339(block["end"])))
    intervals.sort(key=lambda i: (i.start, i.end))
    return intervals


@dataclass(frozen=True)
class MeetEventRequest:
    """The details of a calendar event to create with a Google Meet conference attached."""

    summary: str
    start: datetime
    end: datetime
    timezone: str
    guest_email: str


def build_meet_event_body(request: MeetEventRequest, request_id: str) -> dict[str, Any]:
    """Build an ``events().insert()`` body that asks Google to attach a Meet conference.

    Send this with ``conferenceDataVersion=1`` so the ``conferenceData.createRequest`` is honored.
    """
    return {
        "summary": request.summary,
        "start": {"dateTime": request.start.isoformat(), "timeZone": request.timezone},
        "end": {"dateTime": request.end.isoformat(), "timeZone": request.timezone},
        "attendees": [{"email": request.guest_email}],
        "conferenceData": {
            "createRequest": {
                "requestId": request_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }


def _rfc3339(value: str) -> datetime:
    """Parse an RFC 3339 timestamp (Google emits ``Z`` or an offset) into aware UTC."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"expected an aware RFC 3339 timestamp, got {value!r}")
    return moment.astimezone(UTC)
