"""Turn SDK/API errors into friendly, localized guest messages (RF-16: never leak internals).

The API already returns a safe envelope, but we never render its raw ``message`` to a guest: we map
the stable machine ``error`` code to our own bilingual copy. An unknown code collapses to a generic
message — so a 500's traceback text or any future internal phrasing can never reach the page.
"""

from __future__ import annotations

from aethercal.booking.i18n import Locale, t
from aethercal.client import AetherCalAPIError

# Map the API's machine error codes (see the server's booking/slots routers) to our message keys.
_CODE_TO_KEY: dict[str, str] = {
    "slot_unavailable": "error_slot_unavailable",
    "forbidden": "error_link_invalid",
    "not_active": "error_not_active",
    "availability_unavailable": "availability_unavailable",
    "not_found": "not_found_body",
}


def friendly_api_error(exc: AetherCalAPIError, locale: Locale) -> str:
    """A safe, localized guest message for an API error — internals are never surfaced."""
    key = _CODE_TO_KEY.get(exc.error, "error_generic")
    return t(locale, key)


def friendly_unexpected(locale: Locale) -> str:
    """A safe, localized message for any non-API failure (network, parsing, bug)."""
    return t(locale, "error_generic")


__all__ = ["friendly_api_error", "friendly_unexpected"]
