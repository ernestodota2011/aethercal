"""AetherCal calendar component: a Reflex wrapper around a custom TSX core.

See `aethercal.ui.calendar` for the `Calendar` component itself, and
`docs/spikes/f0-10-reflex-tsx.md` for the F0-10 spike that validated this approach.
"""

from aethercal.ui.calendar import Calendar, CalendarEvent, EventDropPayload

__all__ = ["Calendar", "CalendarEvent", "EventDropPayload"]
