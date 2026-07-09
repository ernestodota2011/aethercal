"""Unit tests for the pure Google-Calendar transforms (F0-11 spike).

These never touch the network -- they pin the freebusy -> TimeInterval mapping and the Meet event
body, so the risky bits of the integration are covered without live credentials.
"""

from datetime import UTC, datetime

import pytest

from aethercal.core.model import TimeInterval
from aethercal.server.integrations.google.parse import (
    MeetEventRequest,
    build_meet_event_body,
    parse_freebusy,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_parse_freebusy_maps_and_sorts_blocks_across_z_and_offset_forms() -> None:
    response = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-07-10T18:00:00Z", "end": "2026-07-10T18:30:00Z"},
                    # explicit -05:00 offset -> 14:00Z..15:00Z, must sort BEFORE the 18:00Z block
                    {"start": "2026-07-10T09:00:00-05:00", "end": "2026-07-10T10:00:00-05:00"},
                ]
            }
        }
    }
    assert parse_freebusy(response, "primary") == [
        TimeInterval(start=_utc(2026, 7, 10, 14, 0), end=_utc(2026, 7, 10, 15, 0)),
        TimeInterval(start=_utc(2026, 7, 10, 18, 0), end=_utc(2026, 7, 10, 18, 30)),
    ]


def test_parse_freebusy_empty_calendar_is_empty() -> None:
    response = {"calendars": {"primary": {"busy": []}}}
    assert parse_freebusy(response, "primary") == []


def test_parse_freebusy_raises_on_calendar_errors_instead_of_reporting_all_free() -> None:
    response = {"calendars": {"primary": {"errors": [{"reason": "notFound"}], "busy": []}}}
    with pytest.raises(RuntimeError, match="notFound"):
        parse_freebusy(response, "primary")


def test_build_meet_event_body_requests_hangouts_meet() -> None:
    body = build_meet_event_body(
        MeetEventRequest(
            summary="AetherCal discovery call",
            start=_utc(2026, 7, 15, 16, 0),
            end=_utc(2026, 7, 15, 16, 30),
            timezone="America/New_York",
            guest_email="lead@example.com",
        ),
        request_id="spike-req-1",
    )
    assert body["summary"] == "AetherCal discovery call"
    assert body["attendees"] == [{"email": "lead@example.com"}]
    create = body["conferenceData"]["createRequest"]
    assert create["requestId"] == "spike-req-1"
    assert create["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert body["start"]["timeZone"] == "America/New_York"
