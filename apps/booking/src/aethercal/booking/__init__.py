"""AetherCal public booking page (FastHTML + HTMX): the stateless guest-facing booking app.

The app reaches the AetherCal API only through the SDK (the D4 rule) with a server-side API key, so
a guest never sees a key. ``create_app`` builds the FastHTML app from settings and an SDK factory.
"""

from __future__ import annotations

from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings

__all__ = ["BookingSettings", "create_app"]
