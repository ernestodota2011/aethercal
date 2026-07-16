"""Console entrypoint: ``python -m aethercal.booking`` serves the booking app with uvicorn.

Reads its configuration from the environment (``AETHERCAL_API_URL``, ``AETHERCAL_TENANT_SLUG``,
``AETHERCAL_TURNSTILE_SITE_KEY``, plus the optional ``AETHERCAL_BOOKING_HOST`` /
``AETHERCAL_BOOKING_PORT``), builds a real (non-mock) SDK ``client_factory``, and runs the ASGI app.

==There is no ``AETHERCAL_API_KEY`` any more.== This process held a key with the tenant's full
permissions and is the one exposed to the open internet; it now talks to the public API and holds no
credential at all. Nothing here to leak, and nothing here to rotate.
"""

from __future__ import annotations

import os

import uvicorn
from fasthtml.common import FastHTML

from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001


def build_app() -> FastHTML:
    """Construct the booking app from the environment with a real (networked) SDK factory."""
    settings = BookingSettings.from_env(os.environ)
    return create_app(
        settings=settings,
        # No `api_key=`. The page is an anonymous client now — see the module docstring.
        client_factory=lambda: AetherCalClient(settings.api_url),
    )


def main() -> None:
    """Serve the booking app over HTTP (host/port from the environment)."""
    host = os.environ.get("AETHERCAL_BOOKING_HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("AETHERCAL_BOOKING_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    uvicorn.run(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
