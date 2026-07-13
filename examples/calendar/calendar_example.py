"""A minimal Reflex app that renders the AetherCal calendar.

It shows the pieces you actually need: a controlled calendar (the host owns `view` and `anchor`), a
drag that reaches your backend, the resource timeline, theming and i18n.

Run it:

    pip install "aethercal-ui[reflex]" reflex
    reflex init          # once, in this directory
    reflex run

Then open http://localhost:3000.

On Windows, importing this module makes Reflex symlink the component's shared asset, which Windows
refuses without symlink privilege (WinError 1314): enable Developer Mode first. Linux and macOS are
unaffected.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from aethercal.ui import Calendar, CalendarEvent, CalendarResource

# Use the exported TypedDicts, not bare dicts: Reflex type-checks a prop against its declared type,
# so a state field annotated `list[dict]` is REJECTED at build time ("Invalid var passed for prop
# Calendar.events"). Annotate with CalendarEvent / CalendarResource and it binds cleanly.
#
# Events carry naive LOCAL wall-time (no offset, no "Z") — the calendar renders the clock time you
# hand it. AetherCal's API speaks UTC, so convert at that boundary, not here.
INITIAL_EVENTS: list[CalendarEvent] = [
    {
        "id": "1",
        "title": "Intro call — Jane Doe",
        "start": "2026-07-13T09:00:00",
        "end": "2026-07-13T09:30:00",
        "resourceId": "rivera",
    },
    {
        "id": "2",
        "title": "Follow-up — Acme",
        "start": "2026-07-13T11:00:00",
        "end": "2026-07-13T12:00:00",
        "resourceId": "nakamura",
    },
    {
        "id": "3",
        "title": "Blocked",
        "start": "2026-07-14T14:00:00",
        "end": "2026-07-14T15:30:00",
        "editable": False,
        "resourceId": "rivera",
    },
]

# The timeline's rows. A resource is generic: AetherCal maps one to a host, but the component would
# just as happily render rooms or machines. `groupId` is both the grouping key and the header label.
RESOURCES: list[CalendarResource] = [
    {"id": "rivera", "title": "Dr. Rivera", "groupId": "Clinic A"},
    {"id": "nakamura", "title": "Dr. Nakamura", "groupId": "Clinic A"},
    {"id": "oyelaran", "title": "Dr. Oyelaran", "groupId": "Clinic B"},
]


class State(rx.State):
    """The calendar is CONTROLLED: this state owns the visible period and the view."""

    view: str = "week"
    anchor: str = "2026-07-13"
    events: list[CalendarEvent] = INITIAL_EVENTS
    status: str = "Drag an event to move it."

    @rx.event
    def on_event_drop(self, payload: dict[str, Any]) -> None:
        """A drag finished. Persist it — here it just moves in local state."""
        moved: list[CalendarEvent] = []
        for event in self.events:
            if event["id"] != payload["id"]:
                moved.append(event)
                continue
            updated: CalendarEvent = {**event, "start": payload["start"], "end": payload["end"]}
            # On the timeline, a cross-row drag also names the TARGET resource row.
            if "resourceId" in payload:
                updated["resourceId"] = payload["resourceId"]
            moved.append(updated)
        self.events = moved
        self.status = f"Moved {payload['id']} to {payload['start']}."

    @rx.event
    def on_range_change(self, payload: dict[str, Any]) -> None:
        """Previous / today / next moved the period: `from` is the new anchor."""
        self.anchor = payload["from"]

    @rx.event
    def on_view_change(self, payload: dict[str, Any]) -> None:
        """The view switcher changed the view, and reports that view's period too."""
        self.view = payload["view"]
        self.anchor = payload["from"]


def index() -> rx.Component:
    return rx.vstack(
        rx.heading("AetherCal", size="6"),
        rx.text(State.status),
        Calendar.create(
            view=State.view,
            anchor=State.anchor,
            events=State.events,
            resources=RESOURCES,
            timeline_days=5,
            locale="es",
            theme="dark",
            first_day_of_week=1,
            navigation=True,
            on_event_drop=State.on_event_drop,
            on_range_change=State.on_range_change,
            on_view_change=State.on_view_change,
        ),
        width="100%",
        padding="1.5rem",
        spacing="4",
    )


app = rx.App()
app.add_page(index, title="AetherCal calendar example")
