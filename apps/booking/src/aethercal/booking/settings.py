"""Environment-driven configuration for the public booking page.

.. rubric:: ==THE API KEY IS GONE.==

This page used to hold one — server-side, never shown to the guest, but a key with the tenant's FULL
permissions, sitting in the most exposed process in the system. It could read every booking a
business had ever taken, with the guest's name, e-mail and notes on each, and it could write
anything. It was also what made the page MONO-BUSINESS by construction: a key names exactly one.

The public API (``/api/v1/public/*``) removes the need for it, so the field is DELETED rather than
deprecated. The risk is not mitigated: it is gone. In its place the page carries ``tenant_slug`` —
the business it serves by default — and serves any other business named in the ROUTE, which is what
lets ONE deployment host N businesses.

``turnstile_site_key`` is the captcha's PUBLIC half (the private half lives on the API, which
refuses
to boot without it). Absent, the page renders no widget, submits no token — and every booking is
then
refused by the API. The page can fail to ASK the question; it can never answer it.

Stateless by design: no session store, and nothing here reaches the network — ``from_env`` is a pure
mapping read, which keeps it trivially testable.
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
#: IP in ``CF-Connecting-IP``.
#:
#: SECURE BY DEFAULT: this is EMPTY. With no configured proxy, NO peer is trusted, so the header is
#: never honored and the transport address is always the rate-limit identity — a direct client
#: cannot forge ``CF-Connecting-IP`` to spoof its identity (evading the limit or inflating the
#: limiter's keyspace). ⚠️ PRODUCTION behind a reverse proxy (NPM/Cloudflare/compose) MUST set
#: ``AETHERCAL_BOOKING_TRUSTED_PROXIES`` to the proxy's CONCRETE CIDR (e.g. the compose network or
#: the NPM host address) — otherwise every guest collapses onto the proxy's single IP and shares
#: one rate-limit budget. The value is a comma-separated CIDR list, parsed by
#: :func:`_parse_trusted_proxies`.
DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = ()

#: Origins allowed to frame ``/embed/*`` via CSP ``frame-ancestors`` (B0) — a comma-separated
#: allow-list from ``AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS``. EMPTY by default: v1 trusts the
#: operator to lock this down once real embedders are known; ``security_headers`` (app.py) treats
#: an empty tuple as ``frame-ancestors *`` for `/embed/*` — everywhere ELSE stays `'self'`-only.
DEFAULT_EMBED_ALLOWED_ORIGINS: tuple[str, ...] = ()

_ENV_API_URL = "AETHERCAL_API_URL"
_ENV_TENANT_SLUG = "AETHERCAL_TENANT_SLUG"
_ENV_TURNSTILE_SITE_KEY = "AETHERCAL_TURNSTILE_SITE_KEY"
_ENV_DEFAULT_LOCALE = "AETHERCAL_BOOKING_DEFAULT_LOCALE"
_ENV_BASE_URL = "AETHERCAL_BOOKING_BASE_URL"
_ENV_TRUSTED_PROXIES = "AETHERCAL_BOOKING_TRUSTED_PROXIES"
_ENV_EMBED_ALLOWED_ORIGINS = "AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS"


@dataclass(frozen=True, slots=True)
class BookingSettings:
    """Immutable runtime configuration for the booking app."""

    api_url: str
    #: The business this deployment serves when the route does not name one. ``None`` = the page has
    #: no default and every route must carry a business (``/t/{tenant_slug}/...``).
    tenant_slug: str | None
    #: The captcha's PUBLIC key. ``None`` renders no widget — which is not a bypass: the API
    #: verifies
    #: server-side and refuses a booking with no token.
    turnstile_site_key: str | None
    default_locale: Locale
    base_url: str = DEFAULT_BASE_URL
    trusted_proxies: tuple[str, ...] = DEFAULT_TRUSTED_PROXIES
    embed_allowed_origins: tuple[str, ...] = DEFAULT_EMBED_ALLOWED_ORIGINS

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BookingSettings:
        """Build settings from an environment mapping (defaults applied, secrets never logged)."""
        api_url = environ.get(_ENV_API_URL, DEFAULT_API_URL).strip().rstrip("/") or DEFAULT_API_URL
        tenant_slug = environ.get(_ENV_TENANT_SLUG, "").strip() or None
        turnstile_site_key = environ.get(_ENV_TURNSTILE_SITE_KEY, "").strip() or None
        default_locale = normalize_locale(environ.get(_ENV_DEFAULT_LOCALE)) or DEFAULT_LOCALE
        base_url = (
            environ.get(_ENV_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        )
        trusted_proxies = _parse_csv(environ.get(_ENV_TRUSTED_PROXIES), DEFAULT_TRUSTED_PROXIES)
        embed_allowed_origins = _parse_csv(
            environ.get(_ENV_EMBED_ALLOWED_ORIGINS), DEFAULT_EMBED_ALLOWED_ORIGINS
        )
        return cls(
            api_url=api_url,
            tenant_slug=tenant_slug,
            turnstile_site_key=turnstile_site_key,
            default_locale=default_locale,
            base_url=base_url,
            trusted_proxies=trusted_proxies,
            embed_allowed_origins=embed_allowed_origins,
        )


def _parse_csv(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env value into a tuple, dropping blanks; an unset or all-blank
    value falls back to ``default`` (shared by ``TRUSTED_PROXIES`` and ``EMBED_ALLOWED_ORIGINS``,
    which both parse "CSV list of strings, blanks dropped, empty -> a named default")."""
    if not raw:
        return default
    parsed = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parsed or default


__all__ = [
    "DEFAULT_API_URL",
    "DEFAULT_BASE_URL",
    "DEFAULT_EMBED_ALLOWED_ORIGINS",
    "DEFAULT_TRUSTED_PROXIES",
    "BookingSettings",
]
