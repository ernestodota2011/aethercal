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
#
# ==Only ``free`` is free; EVERYTHING else is busy, including statuses this code has never seen.==
# The list below therefore exists to DOCUMENT the states we know, not to allow-list the busy ones:
# an allow-list fails open exactly once — the day Graph adds a status (or returns ``unknown``), the
# new string is not in it and the host's calendar reads as empty, which is the double-booking this
# integration exists to prevent. A deny-list of one cannot rot.
GRAPH_FREE_STATUS = "free"

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


def _select_schedule_entry(
    entries: list[dict[str, Any]], schedule_id: str | None
) -> dict[str, Any]:
    """The entry for ``schedule_id`` (or the first one), refusing to guess when it is missing."""
    if schedule_id is None:
        return entries[0]
    target_norm = schedule_id.strip().lower()
    for entry in entries:
        if str(entry.get("scheduleId", "")).strip().lower() == target_norm:
            return entry
    raise RuntimeError(
        f"Microsoft Graph response omitted schedule {schedule_id!r}; "
        f"refusing to treat as free (prevents double-booking)"
    )


def _busy_intervals(target_entry: dict[str, Any], schedule_id: str | None) -> list[TimeInterval]:
    """Every interval the entry says the host is NOT free, with fail-closed parsing.

    ==Only an EXPLICIT ``free`` status is free.== Every other status -- ``busy``, ``tentative``,
    ``oof``, ``workingElsewhere``, ``unknown``, or any status this code has never seen -- blocks
    time. A non-free item whose instants cannot be read or whose end precedes its start is refused
    rather than skipped: the host may be busy at a moment this parser cannot name, and guessing it
    is how a double-booking ships.
    """
    raw_items = target_entry.get("scheduleItems")
    if not isinstance(raw_items, list):
        raise RuntimeError(
            "Microsoft Graph schedule entry carried no usable 'scheduleItems' list; refusing to "
            "treat the calendar as free (prevents double-booking)"
        )

    intervals: list[TimeInterval] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise RuntimeError(
                "Microsoft Graph returned a scheduleItem that is not an object; refusing to guess "
                "the host's availability (prevents double-booking)"
            )
        status = str(item.get("status", "")).strip().lower()
        if status == GRAPH_FREE_STATUS:
            continue
        start_block = item.get("start")
        end_block = item.get("end")
        raw_start = start_block.get("dateTime") if isinstance(start_block, dict) else None
        raw_end = end_block.get("dateTime") if isinstance(end_block, dict) else None
        if not raw_start or not raw_end:
            raise RuntimeError(
                f"Microsoft Graph returned a non-free scheduleItem (status={status!r}) with no "
                "valid start/end instants; the host may be busy at a time this parser cannot name, "
                "so refusing to treat the calendar as free (prevents double-booking)"
            )
        start_dt = _parse_graph_datetime(str(raw_start))
        end_dt = _parse_graph_datetime(str(raw_end))
        if end_dt < start_dt:
            raise RuntimeError(
                f"Microsoft Graph returned a scheduleItem (status={status!r}) whose end precedes "
                "its start; refusing to guess the host's availability (prevents double-booking)"
            )
        if end_dt > start_dt:
            intervals.append(TimeInterval(start=start_dt, end=end_dt))

    intervals.sort(key=lambda interval: (interval.start, interval.end))
    return intervals


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
    - The schedule entry carries no usable ``scheduleItems`` list, or a non-free item with no
      valid start/end instants, or one whose end precedes its start.

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

    target_entry = _select_schedule_entry(entries, schedule_id)
    entry_error = target_entry.get("error")
    if entry_error:
        raise RuntimeError(
            f"Microsoft Graph reported error for schedule {schedule_id or 'primary'!r}: "
            f"{entry_error}"
        )

    return _busy_intervals(target_entry, schedule_id)


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
