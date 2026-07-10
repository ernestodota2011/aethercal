"""Environment-driven configuration for the public booking page.

Stateless by design: the app holds no session store, only the API base URL it talks to and the
**server-side** API key it presents on the guest's behalf (the guest never sees a key — the D4
rule). Both come from the environment so no secret is ever written in source. Nothing here reaches
the network; ``BookingSettings.from_env`` is a pure mapping read, which keeps it trivially testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aethercal.booking.i18n import DEFAULT_LOCALE, Locale, normalize_locale

#: Where the booking page looks for the API when ``AETHERCAL_API_URL`` is unset (local dev).
DEFAULT_API_URL = "http://127.0.0.1:8000"

_ENV_API_URL = "AETHERCAL_API_URL"
_ENV_API_KEY = "AETHERCAL_API_KEY"
_ENV_DEFAULT_LOCALE = "AETHERCAL_BOOKING_DEFAULT_LOCALE"


@dataclass(frozen=True, slots=True)
class BookingSettings:
    """Immutable runtime configuration for the booking app."""

    api_url: str
    api_key: str | None
    default_locale: Locale

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BookingSettings:
        """Build settings from an environment mapping (defaults applied, secrets never logged)."""
        api_url = environ.get(_ENV_API_URL, DEFAULT_API_URL).strip().rstrip("/") or DEFAULT_API_URL
        raw_key = environ.get(_ENV_API_KEY, "").strip()
        api_key = raw_key or None
        default_locale = normalize_locale(environ.get(_ENV_DEFAULT_LOCALE)) or DEFAULT_LOCALE
        return cls(api_url=api_url, api_key=api_key, default_locale=default_locale)


__all__ = ["DEFAULT_API_URL", "BookingSettings"]
