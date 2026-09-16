"""Pure transforms for the Microsoft 365 / Graph API calendar integration (Horizon 1).

No network, strictly typed, and fully unit-testable offline:
- Transforms Microsoft Graph `getSchedule` responses into `TimeInterval` busy blocks.
- Implements the RF-13 fail-closed rule: UNKNOWN/UNPARSABLE IS NEVER FREE.
- Builds event bodies with Microsoft Teams meeting integration (`isOnlineMeeting`).
- Extracts join URLs from Graph event representations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aethercal.core.model import TimeInterval

# Statuses in Graph API `scheduleItems` that block a host's availability.
# Statuses: "free", "tentative", "busy", "oof" (Out of Office), "workingElsewhere", "unknown".
# In accordance with RF-13 (fail-closed anti-double-booking), any status indicating
# non-free time or away time is treated as busy.
GRAPH_BUSY_STATUSES: frozenset[str] = frozenset({"busy", "tentative", "oof", "workingelsewhere"})

# Regex to normalize Graph's 7-digit subsecond precision (e.g. .0000000Z)
# down to 6 digits for Python.
_SUBSECOND_RE = re.compile(r"(\.\d{6})\d+")


def _parse_graph_datetime(value: str) -> datetime:
    """Parse a Graph API ISO datetime into aware UTC.

    Graph often returns 7 fractional digits (e.g. 2026-09-16T14:00:00.0000000Z).
    Python's `datetime.fromisoformat` accepts up to 6 digits (microseconds).
    """
    cleaned = value.strip().replace("Z", "+00:00")
    cleaned = _SUBSECOND_RE.sub(r"\1", cleaned)
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_graph_schedule(
    response: dict[str, Any],
    schedule_id: str | None = None,
) -> list[TimeInterval]:
    """Map a host's busy blocks from a Graph `getSchedule` response into sorted UTC `TimeInterval`s.

    Raises RuntimeError if:
    - The response contains an API-level error.
    - `value` is absent or empty.
    - `schedule_id` was specified and is absent from the schedule entries.
    - The matched schedule entry carries a per-schedule error.

    Refusing to return an empty list on error prevents silent double-booking (RF-13).
    """
    if "error" in response:
        raise RuntimeError(
            f"Microsoft Graph getSchedule returned top-level error: {response['error']}"
        )

    entries: list[dict[str, Any]] = response.get("value", [])
    if not entries:
        raise RuntimeError(
            "Microsoft Graph getSchedule response contained no schedule entries; "
            "refusing to treat calendar as free"
        )

    target_entry: dict[str, Any] | None = None
    if schedule_id is not None:
        target_norm = schedule_id.strip().lower()
        for entry in entries:
            entry_id = str(entry.get("scheduleId", "")).strip().lower()
            if entry_id == target_norm:
                target_entry = entry
                break
        if target_entry is None:
            raise RuntimeError(
                f"Microsoft Graph response omitted schedule {schedule_id!r}; "
                f"refusing to treat as free (prevents double-booking)"
            )
    else:
        target_entry = entries[0]

    entry_error = target_entry.get("error")
    if entry_error:
        raise RuntimeError(
            f"Microsoft Graph reported error for schedule {schedule_id or 'primary'!r}: "
            f"{entry_error}"
        )

    intervals: list[TimeInterval] = []
    items: list[dict[str, Any]] = target_entry.get("scheduleItems", [])
    for item in items:
        status = str(item.get("status", "")).strip().lower()
        if status in GRAPH_BUSY_STATUSES:
            raw_start = item.get("start", {}).get("dateTime")
            raw_end = item.get("end", {}).get("dateTime")
            if not raw_start or not raw_end:
                continue
            start_dt = _parse_graph_datetime(raw_start)
            end_dt = _parse_graph_datetime(raw_end)
            if end_dt > start_dt:
                intervals.append(TimeInterval(start=start_dt, end=end_dt))

    intervals.sort(key=lambda i: (i.start, i.end))
    return intervals


def build_schedule_request_body(
    schedules: list[str],
    window: TimeInterval,
    *,
    availability_view_interval: int = 15,
) -> dict[str, Any]:
    """Build the JSON body for `getSchedule` on `/me/calendar` or `/users/{id}/calendar`."""
    return {
        "schedules": schedules,
        "startTime": {
            "dateTime": window.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
        "endTime": {
            "dateTime": window.end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
        "availabilityViewInterval": availability_view_interval,
    }


@dataclass(frozen=True)
class MicrosoftEventRequest:
    """The specification of an event to insert into Microsoft 365."""

    summary: str
    start: datetime
    end: datetime
    timezone: str
    guest_email: str
    guest_name: str | None = None
    body_content: str | None = None
    is_online_meeting: bool = True
    online_meeting_provider: str = "teamsForBusiness"


def build_graph_event_body(request: MicrosoftEventRequest) -> dict[str, Any]:
    """Build the JSON payload for `POST /me/events` with optional Teams online meeting."""
    body: dict[str, Any] = {
        "subject": request.summary,
        "start": {
            "dateTime": request.start.astimezone(UTC).isoformat(),
            "timeZone": request.timezone,
        },
        "end": {
            "dateTime": request.end.astimezone(UTC).isoformat(),
            "timeZone": request.timezone,
        },
        "attendees": [
            {
                "emailAddress": {
                    "address": request.guest_email,
                    "name": request.guest_name or request.guest_email,
                },
                "type": "required",
            }
        ],
    }
    if request.body_content:
        body["body"] = {
            "contentType": "text",
            "content": request.body_content,
        }
    if request.is_online_meeting:
        body["isOnlineMeeting"] = True
        body["onlineMeetingProvider"] = request.online_meeting_provider

    return body


def extract_teams_join_url(response: dict[str, Any]) -> str | None:
    """Extract the Microsoft Teams join web URL from a Graph event response."""
    online_meeting = response.get("onlineMeeting")
    if isinstance(online_meeting, dict):
        join_url = online_meeting.get("joinUrl")
        if join_url:
            return str(join_url)
    if response.get("onlineMeetingUrl"):
        return str(response["onlineMeetingUrl"])
    return None
