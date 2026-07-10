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

#: The booking page's own public base URL, used to mint ABSOLUTE urls (Open Graph/Twitter Card
#: ``og:url``/``og:image``) — a bare relative path is meaningless to a social unfurler or crawler,
#: which has no request context of its own. Defaults to the agency's production instance;
#: self-hosted deployments override it with ``AETHERCAL_BOOKING_BASE_URL`` (the same variable name
#: the API server reads for guest links, so the two only need to agree once per deployment).
DEFAULT_BASE_URL = "https://book.aetherlogik.com"

#: CIDR ranges whose peer address (``request.client.host``) is trusted to have set the real client
#: IP in ``CF-Connecting-IP``. The app ALWAYS runs behind NPM/compose, so the real transport peer
#: is a private (RFC1918) or loopback address; a direct PUBLIC peer is by definition not our proxy
#: and its ``CF-Connecting-IP`` header must NOT be honored (it would let a client forge its identity
#: to evade the rate limit or inflate the limiter's keyspace). Override per deployment with
#: ``AETHERCAL_BOOKING_TRUSTED_PROXIES`` (a comma-separated CIDR list).
DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = (
    "127.0.0.0/8",  # IPv4 loopback
    "10.0.0.0/8",  # RFC1918 private
    "172.16.0.0/12",  # RFC1918 private
    "192.168.0.0/16",  # RFC1918 private
    "::1/128",  # IPv6 loopback
    "fc00::/7",  # IPv6 unique-local
)

_ENV_API_URL = "AETHERCAL_API_URL"
_ENV_API_KEY = "AETHERCAL_API_KEY"
_ENV_DEFAULT_LOCALE = "AETHERCAL_BOOKING_DEFAULT_LOCALE"
_ENV_BASE_URL = "AETHERCAL_BOOKING_BASE_URL"
_ENV_TRUSTED_PROXIES = "AETHERCAL_BOOKING_TRUSTED_PROXIES"


@dataclass(frozen=True, slots=True)
class BookingSettings:
    """Immutable runtime configuration for the booking app."""

    api_url: str
    api_key: str | None
    default_locale: Locale
    base_url: str = DEFAULT_BASE_URL
    trusted_proxies: tuple[str, ...] = DEFAULT_TRUSTED_PROXIES

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BookingSettings:
        """Build settings from an environment mapping (defaults applied, secrets never logged)."""
        api_url = environ.get(_ENV_API_URL, DEFAULT_API_URL).strip().rstrip("/") or DEFAULT_API_URL
        raw_key = environ.get(_ENV_API_KEY, "").strip()
        api_key = raw_key or None
        default_locale = normalize_locale(environ.get(_ENV_DEFAULT_LOCALE)) or DEFAULT_LOCALE
        base_url = (
            environ.get(_ENV_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        )
        trusted_proxies = _parse_trusted_proxies(environ.get(_ENV_TRUSTED_PROXIES))
        return cls(
            api_url=api_url,
            api_key=api_key,
            default_locale=default_locale,
            base_url=base_url,
            trusted_proxies=trusted_proxies,
        )


def _parse_trusted_proxies(raw: str | None) -> tuple[str, ...]:
    """Parse the comma-separated ``AETHERCAL_BOOKING_TRUSTED_PROXIES`` env value into CIDR strings,
    dropping blanks; an unset or all-blank value falls back to :data:`DEFAULT_TRUSTED_PROXIES`."""
    if not raw:
        return DEFAULT_TRUSTED_PROXIES
    parsed = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parsed or DEFAULT_TRUSTED_PROXIES


__all__ = [
    "DEFAULT_API_URL",
    "DEFAULT_BASE_URL",
    "DEFAULT_TRUSTED_PROXIES",
    "BookingSettings",
]
