"""Thin live Google Calendar API layer for the spike (F0-11).

The untyped google-api-python-client is contained behind an ``Any`` seam; the pure
request/response shaping lives in ``parse.py`` and is unit-tested. These functions do the actual
network calls and are exercised by the live demo in ``spike.py`` (not by unit tests).
"""

from __future__ import annotations

import uuid
from typing import Any

from googleapiclient.discovery import build

from aethercal.core.model import TimeInterval

from .parse import MeetEventRequest, build_meet_event_body, parse_freebusy


def build_service(credentials: Any) -> Any:  # pragma: no cover - live
    """Build a Google Calendar v3 service client from OAuth credentials."""
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def query_busy(  # pragma: no cover - live
    service: Any, calendar_id: str, window: TimeInterval
) -> list[TimeInterval]:
    """Query freebusy for ``calendar_id`` over ``window`` and return its busy intervals."""
    body = {
        "timeMin": window.start.isoformat(),
        "timeMax": window.end.isoformat(),
        "items": [{"id": calendar_id}],
    }
    response: dict[str, Any] = service.freebusy().query(body=body).execute()
    return parse_freebusy(response, calendar_id)


def insert_event_with_meet(  # pragma: no cover - live
    service: Any, calendar_id: str, request: MeetEventRequest
) -> dict[str, Any]:
    """Insert an event with a new Google Meet conference; return the created event."""
    body = build_meet_event_body(request, str(uuid.uuid4()))
    created: dict[str, Any] = (
        service.events()
        .insert(calendarId=calendar_id, body=body, conferenceDataVersion=1, sendUpdates="none")
        .execute()
    )
    return created


def delete_event(service: Any, calendar_id: str, event_id: str) -> None:  # pragma: no cover - live
    """Delete a calendar event (used to leave the calendar clean after the spike demo)."""
    service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="none").execute()
