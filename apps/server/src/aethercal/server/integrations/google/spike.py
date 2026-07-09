"""Live demo runner for the Google Calendar spike (F0-11): OAuth -> freebusy -> insert Meet event.

Run it directly (NOT under pytest) after exporting the agency OAuth Desktop client env vars:

    set -a; . ~/.aetherlogik/secrets/aethercal-google-oauth.env; set +a
    export AETHERCAL_GOOGLE_CALENDAR_ID=primary
    uv run --package aethercal-server python -m aethercal.server.integrations.google.spike

It queries busy blocks for the next 7 days, inserts a throwaway 30-minute event tomorrow with a Meet
link, prints the Meet URL and event link, then deletes the event so the calendar is left clean.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aethercal.core.model import TimeInterval

from .calendar import build_service, delete_event, insert_event_with_meet, query_busy
from .oauth import get_credentials
from .parse import MeetEventRequest

_TOKEN_PATH = Path.home() / ".aetherlogik" / "secrets" / "aethercal-google-token.json"


def main() -> None:  # pragma: no cover - live demo, verified by hand against the real calendar
    calendar_id = os.environ.get("AETHERCAL_GOOGLE_CALENDAR_ID", "primary")
    guest = os.environ.get("AETHERCAL_GOOGLE_TEST_GUEST", "aethercal-spike@example.com")

    creds = get_credentials(_TOKEN_PATH)
    service = build_service(creds)

    now = datetime.now(UTC)
    window = TimeInterval(start=now, end=now + timedelta(days=7))
    busy = query_busy(service, calendar_id, window)
    print(f"freebusy: {len(busy)} busy block(s) in the next 7 days on {calendar_id!r}")
    for block in busy[:10]:
        print(f"  busy {block.start.isoformat()} -> {block.end.isoformat()}")

    start = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    created = insert_event_with_meet(
        service,
        calendar_id,
        MeetEventRequest(
            summary="AetherCal spike (safe to delete)",
            start=start,
            end=start + timedelta(minutes=30),
            timezone="UTC",
            guest_email=guest,
        ),
    )
    event_id = created["id"]
    print(f"inserted event {event_id}")
    print(f"  htmlLink: {created.get('htmlLink')}")
    print(f"  hangoutLink: {created.get('hangoutLink')}")
    entry_points = (created.get("conferenceData") or {}).get("entryPoints") or []
    for entry in entry_points:
        print(f"  conference entryPoint: {entry.get('entryPointType')} -> {entry.get('uri')}")

    delete_event(service, calendar_id, event_id)
    print("deleted the spike event; calendar left clean")


if __name__ == "__main__":
    main()
