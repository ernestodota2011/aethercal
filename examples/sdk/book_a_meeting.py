"""Book a meeting with the AetherCal SDK.

The whole guest flow, end to end: find what is bookable, find a free slot, take it — and show that
taking the same slot twice is refused.

Run it against a running AetherCal (see docs/quickstart.md):

    export AETHERCAL_URL="http://localhost:8000"
    export AETHERCAL_KEY="ack_...."
    python examples/sdk/book_a_meeting.py

Optionally pass the event type's slug; it defaults to the quickstart's `intro-call`:

    python examples/sdk/book_a_meeting.py my-event-slug
"""

from __future__ import annotations

import datetime as dt
import os
import sys

from aethercal.client import AetherCalAPIError, AetherCalClient, AetherCalTransportError
from aethercal.schemas.bookings import BookingCreate
from aethercal.schemas.event_types import EventTypeRead

BASE_URL = os.environ.get("AETHERCAL_URL", "http://localhost:8000")
API_KEY = os.environ.get("AETHERCAL_KEY")
SLUG = sys.argv[1] if len(sys.argv) > 1 else "intro-call"
GUEST_TZ = "America/New_York"


def find_event_type(client: AetherCalClient, slug: str) -> EventTypeRead:
    """Return the bookable event type with ``slug``, or exit with a useful message."""
    try:
        event_types = client.list_event_types()
    except AetherCalTransportError:
        raise SystemExit(f"No AetherCal at {BASE_URL} — is it running?") from None
    except AetherCalAPIError as exc:
        raise SystemExit(f"The API refused the request: {exc}") from None

    for event_type in event_types:
        if event_type.slug == slug:
            return event_type

    available = ", ".join(et.slug for et in event_types) or "(none)"
    raise SystemExit(f"No event type with slug {slug!r}. Available: {available}")


def main() -> None:
    if not API_KEY:
        raise SystemExit("Set AETHERCAL_KEY (issue one with: aethercal-admin issue-api-key).")

    with AetherCalClient(BASE_URL, api_key=API_KEY) as client:
        # 1. What can be booked?
        event_type = find_event_type(client, SLUG)
        print(f"Event type: {event_type.title} ({event_type.duration_seconds // 60} min)")

        # 2. When is it free? Slot bounds come back in UTC; `tz` is only the display zone.
        today = dt.datetime.now(tz=dt.UTC).date()
        slots = client.get_slots(
            event_type.id,
            window_from=today,
            window_to=today + dt.timedelta(days=14),
            tz=GUEST_TZ,
        )

        # `availability` is "ok" only when the external busy set was known and complete for the
        # window. Anything else means a connected calendar could not be reached, and AetherCal
        # withholds that host's slots rather than risk a double-booking — so an empty list here is
        # not the same statement as "there is no free time".
        if slots.availability != "ok":
            raise SystemExit(
                f"Availability is {slots.availability!r}: a connected calendar was unreachable."
            )
        if not slots.slots:
            print("No free slots in the next 14 days.")
            return

        print(f"{len(slots.slots)} free slots — taking the first one.")
        first = slots.slots[0]

        # 3. Book it.
        booking = client.create_booking(
            BookingCreate(
                event_type_id=event_type.id,
                start=first.start,
                guest_name="Jane Doe",
                guest_email="jane@example.com",
                guest_timezone=GUEST_TZ,
                guest_notes="Booked from examples/sdk/book_a_meeting.py",
            )
        )
        print(f"Booked {booking.id} — {booking.status.value} — {booking.start} to {booking.end}")

        # 4. That slot is gone now. Booking it again is a 409 — and that is the NORMAL race, not an
        #    edge case: between listing the slots and taking one, somebody else can take it first.
        #    Every real integration handles this by re-fetching the slots.
        try:
            client.create_booking(
                BookingCreate(
                    event_type_id=event_type.id,
                    start=first.start,
                    guest_name="Someone Else",
                    guest_email="someone@example.com",
                    guest_timezone=GUEST_TZ,
                )
            )
        except AetherCalAPIError as exc:
            if exc.status_code != 409:
                raise
            print(f"Re-booking the same slot was refused with {exc.status_code}, as it should be.")
        else:
            raise SystemExit("The same slot was booked twice. That is a bug — please report it.")


if __name__ == "__main__":
    main()
