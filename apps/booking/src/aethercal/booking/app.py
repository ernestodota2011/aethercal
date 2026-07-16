"""The FastHTML application: stateless, SSR + HTMX, wired to the API only through the SDK.

The app never touches the database — it calls the AetherCal API on the guest's behalf. ==It holds
no API key at all any more:== it used to carry one with the tenant's full permissions, in the most
exposed process in the system, which is also what made it serve exactly one business (a key names
one). It is now an ANONYMOUS client of the PUBLIC API, and the business travels in the route
(``/t/{tenant_slug}/…``, falling back to ``AETHERCAL_TENANT_SLUG`` for the single-business
self-hoster's unprefixed URLs) — so one deployment serves N businesses. ``create_app`` builds a
:class:`_BookingApp` (settings + a ``client_factory`` returning a fresh :class:`AetherCalClient`)
and wires its handlers as routes; tests inject an ``httpx.MockTransport``-backed client to run the
whole app offline.

The routes deliver the ≤3-step flow (RF-07): an event landing with a slot picker → a details form →
a confirmation, plus token-authorized ``/cancel`` and ``/reschedule`` pages (RF-09). Blocking SDK
calls run in a threadpool so they never stall the event loop. Every failure degrades to a friendly,
localized page — a stack trace or internal message never reaches a guest (RF-16).

The app owns its own security headers (A5.3) — set on every response by
``_SecurityHeadersMiddleware`` via ``security_headers`` — rather than depending on an edge/CDN
config, so the page is correct and portable behind any reverse proxy. It also serves its own
static assets (self-hosted htmx + the externalized tz-detect script, A5.1/A5.2) from ``/static``,
mounted from ``STATIC_DIR``, so the page has no third-party CDN dependency and its
``script-src`` can be a strict ``'self'``.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from fasthtml.common import FastHTML
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.staticfiles import StaticFiles

from aethercal.booking import views
from aethercal.booking.errors import friendly_api_error, friendly_unexpected
from aethercal.booking.forms import BookingRequest, build_booking, parse_questions
from aethercal.booking.i18n import SUPPORTED_LOCALES, Locale, select_locale, t
from aethercal.booking.settings import BookingSettings
from aethercal.booking.timefmt import format_day_heading, format_time, group_slots, today_in_zone
from aethercal.client import AetherCalAPIError, AetherCalClient
from aethercal.core.tz import require_iana_zone
from aethercal.schemas.event_types import resolve_title
from aethercal.schemas.public import PublicEventTypeRead

T = TypeVar("T")

#: Server-side logger for backend-failure observability. The RF-16 trust boundaries degrade the
#: guest experience to a friendly page, but every swallowed failure is logged here (with its
#: traceback) so operators can see a failing backend — the log never reaches the guest.
logger = logging.getLogger(__name__)

#: The status a slot-conflict `AetherCalAPIError` carries — the PRG-redirect trigger (I4).
HTTP_409_CONFLICT = 409
#: The default display zone when a guest hasn't chosen one yet (the browser then auto-detects).
DEFAULT_TZ = "UTC"
#: How many days of availability a single window shows (and the prev/next navigation step).
WINDOW_DAYS = 14
#: Curated zones offered in the selector (Americas-heavy for the Latino ICP); the guest's detected
#: zone is always added client-side if it's missing.
COMMON_TIMEZONES: tuple[str, ...] = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Mexico_City",
    "America/Bogota",
    "America/Lima",
    "America/Santiago",
    "America/Argentina/Buenos_Aires",
    "America/Sao_Paulo",
    "UTC",
    "Europe/Madrid",
    "Europe/London",
    "Europe/Paris",
)

#: The ``static/`` directory next to this module — the vendored htmx bundle and the tz-detect
#: script (A5.1/A5.2), served by the app itself so it has no third-party CDN dependency.
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: ``Cache-Control`` for ``GET /embed.js`` (B2.2) — a year, ``immutable``: the loader changes
#: rarely and integrators paste the ``<script src>`` once. ``docs/embedding.md`` documents the
#: cache-busting escape hatch (append ``?v=<n>`` to the URL) for the rare breaking update.
EMBED_JS_CACHE_CONTROL = "public, max-age=31536000, immutable"


# --------------------------------------------------------------------------------------
# Security headers (A5.3) — the app owns these outright rather than relying on an edge/CDN
# config (portable, OSS-friendly, and correct even when the app is embedded behind a different
# reverse proxy). ``script-src 'self'`` is strict — no CDN, no inline script — made possible by
# self-hosting htmx (A5.1) and externalizing the timezone-detection script (A5.2).
#
# `/embed/*` (B0) is the one deliberate exception: those routes are MEANT to be framed, so their
# CSP `frame-ancestors` is relaxed to the operator's allow-list (or `*` when unset) and they carry
# no `X-Frame-Options` at all (any value there would still block ALL framing, including the
# allow-listed embedder — `X-Frame-Options` predates and can't express an allow-list, so it must be
# dropped, not loosened). Everything else about `/embed/*`'s headers — nosniff, referrer policy,
# HSTS, `script-src` — stays exactly as strict as the baseline, plus one CSP hash source for the
# page's own static inline auto-resize script (views.EMBED_RESIZE_SCRIPT).
# --------------------------------------------------------------------------------------


#: Cloudflare's origin for the Turnstile widget. ==The ONE third-party origin this page will ever
#: talk to==, and it is named explicitly rather than loosening `script-src` to something like
#: `https:` — a CSP that admits "any HTTPS script" admits every HTTPS script.
TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"


def _content_security_policy(
    *, frame_ancestors: str, script_src_extra: str = "", turnstile: bool = False
) -> str:
    """Build the CSP shared by the baseline and the ``/embed/*`` variant.

    ``turnstile`` widens the policy for the captcha, and for the captcha ONLY: its loader is a
    script
    from Cloudflare's origin, and the challenge itself renders in an iframe from that same origin
    (hence ``frame-src``, which ``default-src 'self'`` would otherwise refuse — silently, leaving a
    widget that never appears and a booking flow every guest fails).

    It is added only where it is needed. A captcha the operator has not configured does not buy a
    third-party script source on every page of the site.
    """
    sources = ["'self'"]
    if script_src_extra:
        sources.append(script_src_extra)
    if turnstile:
        sources.append(TURNSTILE_ORIGIN)
    frame_src = f" frame-src {TURNSTILE_ORIGIN}; " if turnstile else " "
    return (
        "default-src 'self'; "
        f"script-src {' '.join(sources)}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        f"frame-ancestors {frame_ancestors};{frame_src}"
        "base-uri 'self'; "
        "form-action 'self'"
    )


_CONTENT_SECURITY_POLICY = _content_security_policy(frame_ancestors="'self'")

_BASE_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=(), browsing-topics=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

#: The path prefix that marks a route as the frameable embed surface (B0/B1) — every path in this
#: space gets relaxed framing headers and the compact chrome-less view shell.
_EMBED_PATH_PREFIX = "/embed/"
#: The equivalent NON-embed routing prefix (the pre-existing booking flow), used to build the
#: correct base path for links/actions/redirects depending on which space a request is in.
_NORMAL_PATH_PREFIX = "/e"


#: The route prefix that names a BUSINESS explicitly — ``/t/{tenant_slug}/e/{event_slug}``. It is
#: what makes one deployment able to serve N businesses: the old page could serve exactly one,
#: because its identity was an API key, and a key names exactly one.
_TENANT_PATH_PREFIX = "/t/"


def _is_embed_path(path: str) -> bool:
    """True for any path in the frameable embed space (B0/B1) — ==including its tenant twin.==

    Decided on SEGMENTS, not on a prefix, and both halves of that matter:

    * a prefix test (``startswith("/embed/")``) would have missed ``/t/{tenant}/embed/{slug}``
      entirely. That path is framed by design, and it would have been served with
      ``frame-ancestors 'self'`` + ``X-Frame-Options: SAMEORIGIN`` — so the multi-business embed,
      the
      whole point of the tenant twins, would simply have refused to render inside its embedder;
    * a substring test (``"/embed/" in path``) fixes that and quietly breaks a business whose event
      type is called ``embed``: ``/e/embed/slots`` would suddenly be "framable", chrome-less, and
      relaxed. The segment is the unit of a route, so the segment is what is compared.
    """
    parts = [segment for segment in path.split("/") if segment]
    if not parts:
        return False
    if parts[0] == "embed":
        return True
    return len(parts) > 2 and parts[0] == "t" and parts[2] == "embed"


def _is_embed_request(request: Request) -> bool:
    return _is_embed_path(request.url.path)


def _booking_prefix(embed: bool, tenant: str | None = None) -> str:
    """The base path every link, action and redirect of a request must stay inside.

    Four spaces, and a response must never leak between them: the plain flow (``/e``), the frameable
    embed (``/embed``), and the business-scoped twins of both (``/t/{slug}/e``,
    ``/t/{slug}/embed``).
    ``tenant`` is the slug when the ROUTE named one, and ``None`` when the request is being served
    by
    the deployment's own default business — in which case the links carry no slug either, and the
    single-business deployment's URLs are exactly what they always were.
    """
    base = f"{_TENANT_PATH_PREFIX}{tenant}" if tenant else ""
    return f"{base}{_EMBED_PATH_PREFIX.rstrip('/')}" if embed else f"{base}{_NORMAL_PATH_PREFIX}"


def _embed_frame_ancestors(embed_allowed_origins: Sequence[str]) -> str:
    """The CSP ``frame-ancestors`` value for an ``/embed/*`` response: the configured allow-list
    (space-separated origins — the CSP source-list syntax) or ``*`` when none is configured. V1
    trusts the operator to lock this down via ``AETHERCAL_BOOKING_EMBED_ALLOWED_ORIGINS`` once the
    real embedder(s) are known."""
    origins = [origin for origin in embed_allowed_origins if origin]
    return " ".join(origins) if origins else "*"


def _embed_security_headers(
    embed_allowed_origins: Sequence[str], *, turnstile: bool = False
) -> dict[str, str]:
    """Headers for a frameable ``/embed/*`` response (B0) — see the module-level note above."""
    headers = {
        key: value for key, value in _BASE_SECURITY_HEADERS.items() if key != "X-Frame-Options"
    }
    headers["Content-Security-Policy"] = _content_security_policy(
        frame_ancestors=_embed_frame_ancestors(embed_allowed_origins),
        script_src_extra=views.EMBED_RESIZE_SCRIPT_CSP_SOURCE,
        turnstile=turnstile,
    )
    return headers


def security_headers(
    path: str, *, embed_allowed_origins: Sequence[str] = (), turnstile: bool = False
) -> dict[str, str]:
    """The security headers for a response to ``path``: the strict baseline (``frame-ancestors
    'self'`` + ``X-Frame-Options: SAMEORIGIN``) for every route EXCEPT ``/embed/*`` (B0), which is
    deliberately frameable — see ``_embed_security_headers``.

    ``turnstile`` is on when the operator has configured a captcha site key. It widens
    ``script-src``
    and ``frame-src`` for Cloudflare's origin and NOTHING else — and if it were forgotten, the
    widget
    would be blocked by the browser, no token would be submitted, and every public booking would be
    refused. A CSP that quietly breaks the only gate in front of an unauthenticated write is worth a
    parameter.
    """
    if _is_embed_path(path):
        return _embed_security_headers(embed_allowed_origins, turnstile=turnstile)
    if not turnstile:
        return dict(_BASE_SECURITY_HEADERS)
    headers = dict(_BASE_SECURITY_HEADERS)
    headers["Content-Security-Policy"] = _content_security_policy(
        frame_ancestors="'self'", turnstile=True
    )
    return headers


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Sets the app-owned security headers (``security_headers``) on every response."""

    def __init__(
        self,
        app: Callable[..., object],
        *,
        turnstile: bool = False,
        embed_allowed_origins: Sequence[str] = (),
    ) -> None:
        super().__init__(app)  # pyright: ignore[reportArgumentType]
        self._turnstile = turnstile
        self._embed_allowed_origins = tuple(embed_allowed_origins)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = security_headers(
            request.url.path,
            embed_allowed_origins=self._embed_allowed_origins,
            turnstile=self._turnstile,
        )
        for name, value in headers.items():
            response.headers[name] = value
        return response


# --------------------------------------------------------------------------------------
# App-level per-IP rate limiting on the public POST handlers — an in-process replacement for the
# Cloudflare rate-limit rule the free plan doesn't allow. The booking app runs single-process
# (`python -m aethercal.booking`), so in-process state is sufficient; it is not shared across
# replicas, which is an accepted trade-off for this deployment shape.
# --------------------------------------------------------------------------------------

#: A generous per-IP threshold — enough headroom for a guest retrying a real booking, low enough
#: to blunt a scripted flood.
_RATE_LIMIT_MAX_REQUESTS = 15
_RATE_LIMIT_WINDOW_SECONDS = 60.0
#: Hard cap on how many distinct client keys are tracked at once. Bounds the limiter's memory so a
#: flood of clients (already reduced to REAL IPs by the ``_client_ip`` trust gate) can't grow it
#: without limit — past the cap, the least-recently-used key is evicted (a DoS / memory-leak fix).
_RATE_LIMIT_MAX_KEYS = 10_000


class _RateLimiter:
    """A sliding-window rate limiter keyed by client identity (in-process, no external store).

    Exposed as an injectable instance (``create_app(..., rate_limiter=...)``) so tests can use a
    small, fast threshold instead of the production default, and so each app instance owns
    independent state (no cross-test bleed without an explicit shared instance).

    State is BOUNDED (memory-safety): a key whose window has fully drained is dropped, an
    opportunistic sweep prunes fully-expired least-recently-used keys on every call, and a hard
    ``max_keys`` cap LRU-evicts beyond it — so the tracked set stays proportional to active clients
    and can never grow without limit.
    """

    def __init__(
        self,
        *,
        max_requests: int = _RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS,
        max_keys: int = _RATE_LIMIT_MAX_KEYS,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        # LRU-ordered: most-recently-used at the end, so the front holds the stalest keys.
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit for ``key`` and report whether it's within the window's limit.

        Expired hits are pruned from the front of ``key``'s list (chronological by construction, so
        a prefix-pop is correct and cheap). The key is re-inserted as most-recently-used; if its
        window is empty it is dropped entirely rather than left as dead state.
        """
        current = now if now is not None else time.monotonic()
        window_start = current - self._window_seconds
        hits = self._hits.pop(key, None) or []  # pop so a re-insert lands at the MRU end
        while hits and hits[0] < window_start:
            hits.pop(0)
        allowed = len(hits) < self._max_requests
        if allowed:
            hits.append(current)
        if hits:  # keep only live state; an empty window leaves no key behind
            self._hits[key] = hits
        self._sweep_expired(window_start)
        self._evict_over_cap()
        return allowed

    def _sweep_expired(self, window_start: float) -> None:
        """Drop fully-expired keys from the LRU front, stopping at the first still-live key.

        The stalest keys sit at the front (least recently used), so this reclaims the keys that
        would otherwise leak — the clients that hit once and never returned — in bounded work.
        """
        while self._hits:
            oldest_key = next(iter(self._hits))
            hits = self._hits[oldest_key]
            if hits and hits[-1] >= window_start:
                break
            del self._hits[oldest_key]

    def _evict_over_cap(self) -> None:
        """Enforce the hard cardinality cap by evicting least-recently-used keys."""
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)

    def key_count(self) -> int:
        """The number of client keys currently tracked (test/observability seam)."""
        return len(self._hits)

    def reset(self) -> None:
        """Clear all recorded hits (test seam)."""
        self._hits.clear()


#: A parsed trusted-proxy network (IPv4 or IPv6 CIDR).
_TrustedNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_trusted_networks(cidrs: Sequence[str]) -> tuple[_TrustedNetwork, ...]:
    """Parse CIDR strings (from settings) into networks; a malformed entry is logged and dropped
    rather than crashing the app at startup (a bad env value must fail safe, not fatal)."""
    networks: list[_TrustedNetwork] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("booking: ignoring invalid trusted-proxy CIDR %r", cidr)
    return tuple(networks)


def _is_trusted_peer(host: str, trusted: Sequence[_TrustedNetwork]) -> bool:
    """True if ``host`` parses as an IP inside any trusted network (a non-IP peer is never
    trusted). ``addr in net`` short-circuits ``False`` across IP versions, so mixing v4/v6 nets is
    safe."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in trusted)


def _normalize_ip(value: str) -> str | None:
    """The canonical string form of ``value`` if it's a valid IP, else ``None``.

    Normalizing collapses equivalent spellings (``2001:0db8::1`` ≡ ``2001:db8::1``) to one limiter
    key, and returning ``None`` for non-IP input lets callers fail safe on a forged/garbage header.
    """
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _client_ip(request: Request, trusted_proxies: Sequence[_TrustedNetwork]) -> str:
    """The rate-limit key for this request: the real client IP, resolved SAFELY.

    ``CF-Connecting-IP`` is honored ONLY when (a) the transport peer (``request.client.host``) is a
    trusted proxy AND (b) the header value is itself a valid IP — otherwise a direct client could
    forge the header to spoof its identity, evading the per-IP limit or inflating the limiter's
    keyspace (a memory-exhaustion vector). The chosen identity is normalized to its canonical IP
    form so equivalent spellings share one key. When the header can't be trusted/parsed, the
    transport address is authoritative (itself normalized when it is a valid IP).
    """
    client = request.client
    peer = client.host if client is not None else "unknown"
    header = request.headers.get("CF-Connecting-IP")
    if header and _is_trusted_peer(peer, trusted_proxies):
        forwarded = _normalize_ip(header)
        if forwarded is not None:
            return forwarded
    return _normalize_ip(peer) or peer


def _is_rate_limited_post(request: Request) -> bool:
    """Whether this request is one of the public state-changing POSTs the limiter guards.

    The write endpoints — ``/e/{slug}/book`` and its ``/embed/{slug}/book`` twin (B1), ``/cancel``,
    ``/reschedule`` — read endpoints and static assets are never limited.
    """
    if request.method != "POST":
        return False
    path = request.url.path
    if path in ("/cancel", "/reschedule"):
        return True
    # ==The business-scoped twins are POSTs too.== `/t/{slug}/e/{event}/book` writes exactly what
    # `/e/{event}/book` writes, and a limiter that only knew the old shape would have left the new
    # one — the multi-business one, the reason this cut exists — completely unguarded.
    return path.endswith("/book") and (
        path.startswith("/e/")
        or path.startswith(_EMBED_PATH_PREFIX)
        or path.startswith(_TENANT_PATH_PREFIX)
    )


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiting applied at the MIDDLEWARE layer — BEFORE the framework parses the body.

    FastHTML eagerly parses the request form to build handler params, so a per-handler check would
    still let a blocked request pay the (attacker-controlled) body-parse cost. Running the limit
    here short-circuits a flood with a 429 before any body is read; the limiter only needs the
    request's IP, never its body.
    """

    def __init__(
        self,
        app: Callable[..., object],
        *,
        limiter: _RateLimiter,
        trusted_proxies: Sequence[_TrustedNetwork],
        settings: BookingSettings,
    ) -> None:
        super().__init__(app)  # pyright: ignore[reportArgumentType]
        self._limiter = limiter
        self._trusted_proxies = trusted_proxies
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _is_rate_limited_post(request) and not self._limiter.allow(
            _client_ip(request, self._trusted_proxies)
        ):
            return self._too_many_requests(request)
        return await call_next(request)

    def _too_many_requests(self, request: Request) -> Response:
        """The friendly, localized 429 for a rate-limited request (never a stack, never a body)."""
        locale = select_locale(
            query_lang=request.query_params.get("lang"),
            accept_language=request.headers.get("accept-language"),
            default=self._settings.default_locale,
        )
        body = views.message_page(
            locale,
            title=t(locale, "app_name"),
            message=t(locale, "error_rate_limited"),
            lang_urls=_lang_links_here(request),
            base_url=self._settings.base_url,
            is_error=True,
        )
        return HTMLResponse(views.render(body), status_code=429)


# --------------------------------------------------------------------------------------
# Pure request/parse helpers (no app state).
# --------------------------------------------------------------------------------------


def _valid_tz(value: str | None) -> str | None:
    """The guest's ``tz`` if it names a real IANA zone, else ``None`` (the caller falls back).

    What a real zone is is NOT decided here — it is decided once, by
    :func:`aethercal.core.tz.require_iana_zone`, the same rule that guards the booking contract, the
    host's profile and ``GET /slots``. This function only DEGRADES its refusal: ``tz`` is a query
    param and a form field, so a guest (or a crawler) can send anything, and a page that cannot read
    the visitor's zone still owes them a page — rendered in ``DEFAULT_TZ``, not a 500.

    The copy that used to live here asked ``ZoneInfo(value)`` and caught
    ``(ZoneInfoNotFoundError, ValueError)``, which is the filesystem's answer to a different
    question: ``?tz=America`` names a DIRECTORY of the tz database, so it raised a raw ``OSError``
    that walked straight past the ``except`` and took the public event page down with it.
    """
    if not value:
        return None
    try:
        return require_iana_zone(value)
    except ValueError:
        return None


def _parse_instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _form_dict(form: FormData) -> dict[str, str]:
    return {key: value for key, value in form.multi_items() if isinstance(value, str)}


def _now() -> datetime:
    """The current instant in UTC — the single clock seam so date logic is testable and correct."""
    return datetime.now(UTC)


def _tz_of(request: Request) -> tuple[str, bool]:
    chosen = _valid_tz(request.query_params.get("tz"))
    return (chosen, True) if chosen else (DEFAULT_TZ, False)


def _today_in(tz: str) -> date:
    """The calendar day it currently is in the VISITOR's ``tz`` — anchors all date navigation.

    Deriving 'today' from the guest's timezone (not the server clock) keeps prev/next/window
    navigation correct for a guest sitting near a date boundary (RF-06).
    """
    return today_in_zone(_now(), tz)


def _window_of(request: Request, today: date) -> date:
    raw = request.query_params.get("from")
    if not raw:
        return today
    try:
        requested = date.fromisoformat(raw)
    except ValueError:
        return today
    return max(requested, today)


def _lang_links(path: str, params: Mapping[str, str]) -> dict[Locale, str]:
    links: dict[Locale, str] = {}
    for candidate in SUPPORTED_LOCALES:
        query = {**{k: v for k, v in params.items() if k != "lang"}, "lang": candidate}
        links[candidate] = f"{path}?{urlencode(query)}"
    return links


def _lang_links_here(request: Request) -> dict[Locale, str]:
    return _lang_links(request.url.path, dict(request.query_params))


def _when_label(instant: datetime, tz: str, locale: Locale) -> str:
    local = instant.astimezone(ZoneInfo(tz))
    day = format_day_heading(local.date(), locale)
    clock = format_time(instant, tz, locale)
    joiner = " at " if locale == "en" else ", "
    return f"{day}{joiner}{clock}"


def _find_event(events: Sequence[PublicEventTypeRead], slug: str) -> PublicEventTypeRead | None:
    """The event type by slug. ==No ``active`` filter any more, and its absence is the fix.==

    The page used to receive every event type — withdrawn ones included — and filter them here, in
    the CLIENT. A server must never lean on its client to enforce what the business decided, and
    with
    the API now answering anonymous callers, "the page filters it" would have BEEN the enforcement.
    The public listing contains only what is on sale (``services/event_types.list_bookable_*``), so
    there is nothing left here to filter.
    """
    return next((e for e in events if e.slug == slug), None)


def _shifted_url(
    path: str, base: Mapping[str, str], anchor: date, delta_days: int, *, floor: date
) -> str:
    # ``floor`` is the visitor's local "today" — the window never navigates before it. It must be
    # derived from the booking timezone (via ``_today_in``), never the server clock.
    new_from = max(anchor + timedelta(days=delta_days), floor)
    return f"{path}?{urlencode({**base, 'from': new_from.isoformat()})}"


def _not_found(request: Request, locale: Locale, *, base_url: str) -> Response:
    # Derived from `request` (not threaded as a param — keeps this at the PLR0913 budget): an
    # /embed/* 404 (e.g. an unknown slug inside the iframe) must stay compact, chrome-less (B1).
    body = views.message_page(
        locale,
        title=t(locale, "not_found_title"),
        message=t(locale, "not_found_body"),
        lang_urls=_lang_links_here(request),
        base_url=base_url,
        is_error=True,
        embed=_is_embed_request(request),
    )
    return HTMLResponse(views.render(body), status_code=404)


def _http_status_for(exc: Exception | None) -> int:
    """The status a guest-facing error page should carry: a clean upstream 4xx, else 503.

    A backend 5xx (or a transport drop / malformed response) surfaces to the guest as a 503
    "temporarily unavailable" — never the raw 500 — while a clean client signal (409 conflict,
    403 bad token, 404) is passed through so caches/monitors read the outcome correctly.
    """
    if isinstance(exc, AetherCalAPIError) and 400 <= exc.status_code < 500:
        return exc.status_code
    return 503


def _register(app: FastHTML, path: str, handler: Callable[..., object], methods: list[str]) -> None:
    """Register a route by explicit call (not the ``@`` decorator) so handlers stay typed."""
    app.route(path, methods=methods)(handler)  # pyright: ignore[reportUnknownMemberType]


class _BookingApp:
    """Holds the settings + SDK factory; its methods are the route handlers (bound to state)."""

    def __init__(
        self,
        settings: BookingSettings,
        client_factory: Callable[[], AetherCalClient],
        *,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._rate_limiter = rate_limiter if rate_limiter is not None else _RateLimiter()
        self._trusted_proxies = _parse_trusted_networks(settings.trusted_proxies)

    def rate_limit_middleware(self) -> Middleware:
        """The per-IP rate-limit middleware bound to this app's limiter/trusted-proxy config, so the
        limiter state stays encapsulated here (``create_app`` only wires the returned object)."""
        return Middleware(
            _RateLimitMiddleware,
            limiter=self._rate_limiter,
            trusted_proxies=self._trusted_proxies,
            settings=self._settings,
        )

    async def _call(self, call: Callable[[AetherCalClient], T]) -> T:
        """Run a (blocking) SDK call in a threadpool with a fresh client, closing it after."""

        def invoke() -> T:
            with self._client_factory() as client:
                return call(client)

        return await run_in_threadpool(invoke)

    def _route_tenant(self, request: Request) -> str | None:
        """The business named in the PATH, or ``None``. ==This is what the LINK SPACE follows.==

        It drives ``_booking_prefix``, and it must NOT fall back to the default: a guest who arrived
        on the unprefixed ``/e/intro`` (the single-business self-hoster's URL) must be answered with
        links that stay on ``/e/intro`` — never bounced onto ``/t/acme/e/intro``. Prepending the
        default slug to a request that did not carry one is exactly the break this cut promised the
        self-hoster it would not cause, and their guests' bookmarks along with it.
        """
        from_route = request.path_params.get("tenant")
        return from_route if isinstance(from_route, str) and from_route else None

    def _tenant(self, request: Request) -> str | None:
        """Which business to actually TALK TO — the ROUTE first, then the deployment's default.

        This drives the API calls and the "is there any business at all?" 404. It DOES fall back to
        the default, which is what lets the single-business deployment serve ``/e/intro`` (no slug
        in the path) against its one configured business. ``None`` means neither a route slug nor
        ``AETHERCAL_TENANT_SLUG`` — nothing to serve, and the page says so rather than guessing.

        The split from :meth:`_route_tenant` is the whole fix: which business we serve, and which
        URL space we keep the guest in, are two different questions — and conflating them is what
        put a plain-route guest onto a ``/t/…`` link.
        """
        return self._route_tenant(request) or self._settings.tenant_slug

    def _guest_ip(self, request: Request) -> str:
        """The guest's real address, resolved through this page's OWN trusted-proxy contract.

        It is then FORWARDED to the API, which believes it only because this page's address is
        inside
        the API's ``AETHERCAL_TRUSTED_PROXIES``. Without that hop, the API would see this page's
        address for every guest on earth: one rate-limit bucket for all of them, one address stamped
        onto every booking, and the per-IP cap exhausted by the page itself — denying service to
        everybody, in silence. It is the same value this page's own limiter already keys on.
        """
        return _client_ip(request, self._trusted_proxies)

    def _locale(self, request: Request, form_lang: str | None = None) -> Locale:
        return select_locale(
            query_lang=form_lang or request.query_params.get("lang"),
            accept_language=request.headers.get("accept-language"),
            default=self._settings.default_locale,
        )

    async def _slots_section(  # noqa: PLR0913 - each is a distinct query/render input; `event_path`
        # is the routing base (B1: "/e/{slug}", "/embed/{slug}", or their /t/{tenant} twins) — not
        # derivable from `event`.
        self,
        event: PublicEventTypeRead,
        tz: str,
        window_from: date,
        today: date,
        locale: Locale,
        *,
        event_path: str,
        tenant: str,
    ) -> object:
        window_to = window_from + timedelta(days=WINDOW_DAYS - 1)
        try:
            result = await self._call(
                lambda c: c.get_public_slots(
                    tenant, event.slug, window_from=window_from, window_to=window_to, tz=tz
                )
            )
            groups = group_slots(result.slots, tz, locale)
            availability = result.availability
        except Exception:
            # RF-16 trust boundary: an API error, a dropped connection, or a malformed slots
            # response degrades to a friendly "unavailable" notice — never a 500/stack to a guest.
            logger.exception("booking page: failed to load slots for %s", event.slug)
            groups, availability = [], "unavailable"
        base = {"tz": tz, "lang": locale}
        return views.slots_section(
            locale,
            event=event,
            groups=groups,
            availability=availability,
            tz=tz,
            book_path=f"{event_path}/book",
            prev_url=_shifted_url(event_path, base, window_from, -WINDOW_DAYS, floor=today),
            next_url=_shifted_url(event_path, base, window_from, WINDOW_DAYS, floor=today),
            # `_window_of` already floors the requested date to `>= today` — equality means
            # further "previous week" navigation would be a no-op (the floor clamps it in place).
            prev_disabled=(window_from <= today),
        )

    async def _events(self, tenant: str) -> list[PublicEventTypeRead] | None:
        """Load the tenant's event types, or ``None`` if the backend can't be reached (RF-16).

        A public-page trust boundary: an API error, a dropped connection, or a malformed response
        must degrade to a friendly page — never a 500/stack. Every SDK failure collapses to
        ``None`` here, and the caller renders the service-unavailable page.
        """
        try:
            return await self._call(lambda c: c.list_public_event_types(tenant))
        except Exception:
            logger.exception("booking page: failed to load event types for %s", tenant)
            return None

    def _service_error(
        self,
        locale: Locale,
        *,
        lang_urls: dict[Locale, str],
        retry_url: str,
        embed: bool = False,
    ) -> Response:
        """The friendly 'service temporarily unavailable' page (503) with a retry affordance."""
        body = views.message_page(
            locale,
            title=t(locale, "app_name"),
            message=t(locale, "error_generic"),
            lang_urls=lang_urls,
            base_url=self._settings.base_url,
            back_url=retry_url,
            back_label=t(locale, "retry"),
            is_error=True,
            embed=embed,
        )
        return HTMLResponse(views.render(body), status_code=503)

    def _error_response(  # noqa: PLR0913 - each kwarg is a distinct rendering axis (copy/nav/embed)
        self,
        locale: Locale,
        *,
        title: str,
        exc: Exception | None,
        lang_urls: dict[Locale, str],
        retry: tuple[str, str] | None = None,
        embed: bool = False,
    ) -> Response:
        """A friendly, localized error page with the correct HTTP status — never leaks internals.

        ``retry`` is an optional ``(url, label)`` affordance shown as a button (e.g. "back to
        times"). A method (not a module function) so it can read ``self._settings.base_url``
        without growing past the PLR0913 argument budget. ``embed`` (B1) keeps a backend failure
        inside an iframe compact instead of suddenly surfacing the full site chrome.
        """
        message = (
            friendly_api_error(exc, locale)
            if isinstance(exc, AetherCalAPIError)
            else friendly_unexpected(locale)
        )
        status = _http_status_for(exc)
        if status >= 500 and exc is not None:
            # A backend 5xx, transport drop, or unexpected error — observable to ops, hidden from
            # the guest. A clean client signal (409/403/404) is expected flow, not an error to log.
            logger.error("booking page: backend failure rendering %r", title, exc_info=exc)
        retry_url, retry_label = retry if retry is not None else (None, None)
        body = views.message_page(
            locale,
            title=title,
            message=message,
            lang_urls=lang_urls,
            base_url=self._settings.base_url,
            back_url=retry_url,
            back_label=retry_label,
            is_error=True,
            embed=embed,
        )
        return HTMLResponse(views.render(body), status_code=status)

    # -- routes -------------------------------------------------------------------------

    async def index(self, request: Request) -> object:
        locale = self._locale(request)
        tenant = self._tenant(request)
        if tenant is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        events = await self._events(tenant)
        if events is None:
            return self._service_error(
                locale, lang_urls=_lang_links_here(request), retry_url=str(request.url)
            )
        # No `active` filter here any more: the PUBLIC listing only ever contains what is on sale.
        # Filtering in the client was never a defence — it was the server trusting its client.
        #
        # `event_base` is the SAME route-derived space `event()` keeps its links inside — passed so
        # every event link the index emits stays on `/t/{tenant}/e/...` (or the self-hoster's plain
        # `/e/...` when the route named no business). Letting `index_page` hardcode `/e` dropped the
        # route tenant and bounced a guest on `/t/{tenant}` onto the DEFAULT business's event.
        embed = _is_embed_request(request)
        return views.index_page(
            locale,
            event_types=events,
            lang_urls=_lang_links_here(request),
            base_url=self._settings.base_url,
            event_base=_booking_prefix(embed, self._route_tenant(request)),
        )

    async def event(self, request: Request) -> object:
        # This SAME handler serves BOTH "/e/{slug}" and "/embed/{slug}" (B1) — reused verbatim, not
        # duplicated. `embed`/`event_path` are derived from the actual request path, so every link
        # this response emits stays inside whichever space the guest is already in.
        locale = self._locale(request)
        slug = str(request.path_params["slug"])
        tenant = self._tenant(request)
        embed = _is_embed_request(request)
        event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
        tz, tz_explicit = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        if tenant is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        events = await self._events(tenant)
        if events is None:
            return self._service_error(
                locale,
                lang_urls=_lang_links_here(request),
                retry_url=str(request.url),
                embed=embed,
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        section = await self._slots_section(
            found, tz, window_from, today, locale, event_path=event_path, tenant=tenant
        )
        # I4 (PRG): a 409 slot-conflict redirect from book_submit lands back here carrying
        # `?err=slot_unavailable` — render it as an inline notice, never re-post on a refresh.
        notice = (
            t(locale, "error_slot_unavailable")
            if request.query_params.get("err") == "slot_unavailable"
            else None
        )
        return views.event_page(
            locale,
            event=found,
            tz=tz,
            tz_options=COMMON_TIMEZONES,
            tz_explicit=tz_explicit,
            window_from=window_from.isoformat(),
            slots=section,
            self_path=event_path,
            slots_endpoint=f"{event_path}/slots",
            lang_urls=_lang_links_here(request),
            notice=notice,
            base_url=self._settings.base_url,
            embed=embed,
        )

    async def slots_partial(self, request: Request) -> object:
        # Same handler for "/e/{slug}/slots" and "/embed/{slug}/slots" (B1) — see `event()`.
        slug = str(request.path_params["slug"])
        tenant = self._tenant(request)
        embed = _is_embed_request(request)
        event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
        if request.headers.get("HX-Request") is None:
            query = request.url.query
            return RedirectResponse(
                f"{event_path}?{query}" if query else event_path, status_code=303
            )
        locale = self._locale(request)
        tz, _ = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        if tenant is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        events = await self._events(tenant)
        if events is None:
            # HTMX swaps only on 2xx: degrade the fragment in place, not with a non-swapping 5xx.
            return views.slots_unavailable_fragment(locale)
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        return await self._slots_section(
            found, tz, window_from, today, locale, event_path=event_path, tenant=tenant
        )

    async def book_form(self, request: Request) -> object:
        # Same handler for "/e/{slug}/book" (GET) and "/embed/{slug}/book" (GET) — see `event()`.
        locale = self._locale(request)
        slug = str(request.path_params["slug"])
        tenant = self._tenant(request)
        embed = _is_embed_request(request)
        event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
        tz, _ = _tz_of(request)
        start = request.query_params.get("start", "")
        if tenant is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        events = await self._events(tenant)
        if events is None:
            return self._service_error(
                locale,
                lang_urls=_lang_links_here(request),
                retry_url=str(request.url),
                embed=embed,
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        instant = _parse_instant(start)
        if instant is None:
            return RedirectResponse(
                f"{event_path}?{urlencode({'tz': tz, 'lang': locale})}", status_code=303
            )
        return views.booking_form_page(
            locale,
            event=found,
            start_iso=start,
            tz=tz,
            when_label=_when_label(instant, tz, locale),
            questions=parse_questions(found.questions),
            values={},
            errors=[],
            action=f"{event_path}/book",
            lang_urls=_lang_links(f"{event_path}/book", {"start": start, "tz": tz}),
            turnstile_site_key=self._settings.turnstile_site_key,
            base_url=self._settings.base_url,
            embed=embed,
        )

    def _honeypot_response(
        self, request: Request, *, form: Mapping[str, str], slug: str, start: str, tz: str
    ) -> Response | None:
        """The honeypot post-parse check for ``book_submit`` (the rate-limit check runs BEFORE the
        body is even parsed, so it is not here). Returns a short-circuit response, or ``None``.
        ``embed``/the routing prefix are derived from ``request`` (not threaded as params — keeps
        this at the PLR0913 budget).
        """
        if form.get(views.HONEYPOT_FIELD_NAME, "").strip():
            # Anti-spam honeypot: a bot filled a field hidden from real guests. Skip the backend
            # entirely and return a plausible "received" 200 — the bot sees success and doesn't
            # retry — never creating a booking, never leaking that it was caught.
            embed = _is_embed_request(request)
            event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
            locale = self._locale(request, form.get("lang"))
            return views.message_page(
                locale,
                title=t(locale, "app_name"),
                message=t(locale, "honeypot_received_message"),
                lang_urls=_lang_links(f"{event_path}/book", {"start": start, "tz": tz}),
                base_url=self._settings.base_url,
                embed=embed,
            )
        return None

    async def _complete_booking(  # noqa: PLR0913 - each kwarg is a distinct input of one flow
        self,
        request: Request,
        *,
        form: Mapping[str, str],
        event: PublicEventTypeRead,
        locale: Locale,
        tz: str,
        tenant: str,
    ) -> object:
        """Validate the submitted form and either create the booking or return the outcome page:
        inline validation errors, a 409-conflict PRG redirect (I4), a friendly backend-failure
        page, or the confirmation. ``event.slug`` and ``form["start"]`` stand in for the
        ``slug``/``start`` the caller already resolved (kept out of the signature for PLR0913).
        ``embed``/the routing prefix are derived from ``request`` for the same reason (B1).
        """
        slug = event.slug
        embed = _is_embed_request(request)
        event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
        start = form.get("start", "")
        questions = parse_questions(event.questions)
        instant = _parse_instant(start)
        label = _when_label(instant, tz, locale) if instant is not None else ""
        lang_urls = _lang_links(f"{event_path}/book", {"start": start, "tz": tz})
        # No `event_type_id`: the appointment is named by the ROUTE the SDK posts to
        # (/public/{tenant}/{event_slug}/bookings). A body field naming it beside a path that
        # already
        # names it is two sources of truth for one fact — and the one that won would decide whose
        # diary a guest's booking landed in.
        booking_request = BookingRequest(start_iso=start, guest_timezone=tz, locale=locale)
        # `collects_phone` is the server's answer to "will anything actually message this number?"
        # It gates BOTH the rendering of the field (views) and the reading of it here — so a phone
        # POSTed against an event type no active rule messages is dropped, never stored (RNF-8).
        result = build_booking(
            booking_request,
            questions=questions,
            form=form,
            collects_phone=event.collects_phone,
        )
        booking_create = result.booking
        if booking_create is None:
            return views.booking_form_page(
                locale,
                event=event,
                start_iso=start,
                tz=tz,
                when_label=label,
                questions=questions,
                values=result.values,
                errors=result.errors,
                action=f"{event_path}/book",
                lang_urls=lang_urls,
                turnstile_site_key=self._settings.turnstile_site_key,
                base_url=self._settings.base_url,
                embed=embed,
            )
        guest_ip = self._guest_ip(request)
        try:
            booking = await self._call(
                lambda c: c.create_public_booking(
                    tenant, slug, booking_create, forwarded_for=guest_ip
                )
            )
        except Exception as exc:
            if isinstance(exc, AetherCalAPIError) and exc.status_code == HTTP_409_CONFLICT:
                # I4 (PRG): redirect back to the picker instead of re-rendering the POST response
                # inline — a guest refresh then re-GETs the picker instead of re-submitting.
                conflict_query = urlencode({"tz": tz, "lang": locale, "err": "slot_unavailable"})
                return RedirectResponse(f"{event_path}?{conflict_query}", status_code=303)
            back = f"{event_path}?{urlencode({'tz': tz, 'lang': locale})}"
            return self._error_response(
                locale,
                title=resolve_title(event, locale),
                exc=exc,
                lang_urls=lang_urls,
                retry=(back, t(locale, "back_to_times")),
                embed=embed,
            )
        return views.confirmation_page(
            locale,
            event=event,
            booking=booking,
            # The API answers with {id, start, end, status} and no personal data at all — see
            # PublicBookingRead. The page does not need it to: the guest typed this address into the
            # very form we are now rendering the answer to.
            guest_email=booking_create.guest_email,
            when_label=label,
            lang_urls=_lang_links_here(request),
            base_url=self._settings.base_url,
            embed=embed,
        )

    async def book_submit(self, request: Request) -> object:
        # Rate limiting runs in _RateLimitMiddleware, BEFORE FastHTML parses the body — so a blocked
        # request never pays the body-parse cost. This handler only sees requests within the limit.
        # Same handler for "/e/{slug}/book" (POST) and "/embed/{slug}/book" (POST) — see `event()`.
        form = _form_dict(await request.form())
        slug = str(request.path_params["slug"])
        tenant = self._tenant(request)
        embed = _is_embed_request(request)
        event_path = f"{_booking_prefix(embed, self._route_tenant(request))}/{slug}"
        tz = _valid_tz(form.get("tz")) or DEFAULT_TZ
        start = form.get("start", "")
        honeypot = self._honeypot_response(request, form=form, slug=slug, start=start, tz=tz)
        if honeypot is not None:
            return honeypot
        locale = self._locale(request, form.get("lang"))
        if tenant is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        events = await self._events(tenant)
        if events is None:
            return self._service_error(
                locale,
                lang_urls=_lang_links(f"{event_path}/book", {"start": start, "tz": tz}),
                retry_url=f"{event_path}?{urlencode({'tz': tz, 'lang': locale})}",
                embed=embed,
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale, base_url=self._settings.base_url)
        return await self._complete_booking(
            request, form=form, event=found, locale=locale, tz=tz, tenant=tenant
        )

    async def cancel_form(self, request: Request) -> object:
        locale = self._locale(request)
        booking_id = _parse_uuid(request.query_params.get("booking", ""))
        token = request.query_params.get("token", "")
        if booking_id is None or not token:
            return views.message_page(
                locale,
                title=t(locale, "cancel_title"),
                message=t(locale, "reschedule_missing_context"),
                lang_urls=_lang_links_here(request),
                base_url=self._settings.base_url,
                is_error=True,
            )
        return views.cancel_confirm_page(
            locale,
            booking_id=booking_id,
            token=token,
            action="/cancel",
            lang_urls=_lang_links_here(request),
            base_url=self._settings.base_url,
        )

    async def cancel_submit(self, request: Request) -> object:
        # Rate limiting runs in _RateLimitMiddleware, before the body is parsed.
        form = _form_dict(await request.form())
        locale = self._locale(request, form.get("lang"))
        booking_id = _parse_uuid(form.get("booking", ""))
        token = form.get("token", "")
        lang_urls = _lang_links("/cancel", {})
        if booking_id is None or not token:
            return views.message_page(
                locale,
                title=t(locale, "cancel_title"),
                message=t(locale, "reschedule_missing_context"),
                lang_urls=lang_urls,
                base_url=self._settings.base_url,
                is_error=True,
            )
        try:
            await self._call(lambda c: c.cancel_booking(booking_id, token=token))
        except Exception as exc:
            return self._error_response(
                locale,
                title=t(locale, "cancel_title"),
                exc=exc,
                lang_urls=lang_urls,
            )
        return views.message_page(
            locale,
            title=t(locale, "cancel_title"),
            message=t(locale, "cancel_done"),
            lang_urls=lang_urls,
            base_url=self._settings.base_url,
        )

    async def reschedule_form(self, request: Request) -> object:
        locale = self._locale(request)
        booking_id = _parse_uuid(request.query_params.get("booking", ""))
        event_id = _parse_uuid(request.query_params.get("event_type", ""))
        token = request.query_params.get("token", "")
        if booking_id is None or event_id is None or not token:
            return views.message_page(
                locale,
                title=t(locale, "reschedule_title"),
                message=t(locale, "reschedule_missing_context"),
                lang_urls=_lang_links_here(request),
                base_url=self._settings.base_url,
                is_error=True,
            )
        tz, tz_explicit = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        window_to = window_from + timedelta(days=WINDOW_DAYS - 1)
        try:
            result = await self._call(
                # ==The token, and this is what keeps RF-09 alive.== The page holds no API key any
                # more, and the reschedule link in a guest's inbox carries a token, a booking id and
                # an event-type ID — no business, no slug — so the picker cannot be rendered through
                # the public, slug-keyed route. `/api/v1/slots` therefore takes the guest's signed
                # RESCHEDULE token as a second door. Verified, never consumed: a token spent on a
                # page render is a guest who may look at the times once and never move their
                # booking.
                lambda c: c.get_slots(
                    event_id, window_from=window_from, window_to=window_to, tz=tz, token=token
                )
            )
            groups = group_slots(result.slots, tz, locale)
            availability = result.availability
        except Exception:
            # RF-16 trust boundary (see _slots_section): degrade instead of leaking a 500.
            logger.exception("booking page: failed to load reschedule slots for %s", event_id)
            groups, availability = [], "unavailable"
        base = {
            "booking": str(booking_id),
            "token": token,
            "event_type": str(event_id),
            "tz": tz,
            "lang": locale,
        }
        section = views.reschedule_section(
            locale,
            groups=groups,
            availability=availability,
            action="/reschedule",
            booking_id=booking_id,
            token=token,
            prev_url=_shifted_url("/reschedule", base, window_from, -WINDOW_DAYS, floor=today),
            next_url=_shifted_url("/reschedule", base, window_from, WINDOW_DAYS, floor=today),
            prev_disabled=(window_from <= today),
        )
        hidden = [
            ("lang", str(locale)),
            ("from", window_from.isoformat()),
            ("booking", str(booking_id)),
            ("token", token),
            ("event_type", str(event_id)),
        ]
        return views.reschedule_page(
            locale,
            tz=tz,
            tz_options=COMMON_TIMEZONES,
            tz_explicit=tz_explicit,
            self_path="/reschedule",
            hidden=hidden,
            section=section,
            lang_urls=_lang_links_here(request),
            base_url=self._settings.base_url,
        )

    async def reschedule_submit(self, request: Request) -> object:
        # Rate limiting runs in _RateLimitMiddleware, before the body is parsed.
        form = _form_dict(await request.form())
        locale = self._locale(request, form.get("lang"))
        booking_id = _parse_uuid(form.get("booking", ""))
        token = form.get("token", "")
        new_start = _parse_instant(form.get("new_start", ""))
        lang_urls = _lang_links("/reschedule", {})
        if booking_id is None or not token or new_start is None:
            return views.message_page(
                locale,
                title=t(locale, "reschedule_title"),
                message=t(locale, "reschedule_missing_context"),
                lang_urls=lang_urls,
                base_url=self._settings.base_url,
                is_error=True,
            )
        try:
            await self._call(
                lambda c: c.reschedule_booking(booking_id, new_start=new_start, token=token)
            )
        except Exception as exc:
            return self._error_response(
                locale,
                title=t(locale, "reschedule_title"),
                exc=exc,
                lang_urls=lang_urls,
            )
        return views.message_page(
            locale,
            title=t(locale, "reschedule_title"),
            message=t(locale, "reschedule_done"),
            lang_urls=lang_urls,
            base_url=self._settings.base_url,
        )

    def robots_txt(self, request: Request) -> Response:
        """A conservative ``robots.txt``: this is a booking TOOL, not indexable content — every
        path is disallowed except the root. Never touches the backend (like ``healthz``)."""
        del request
        body = "User-agent: *\nAllow: /$\nDisallow: /\n"
        return PlainTextResponse(body)

    def embed_js(self, request: Request) -> Response:
        """Serve the embed loader (B2) at a clean, memorable URL — ``/embed.js``, not just
        ``/static/embed.js`` — the way any third-party widget script is conventionally referenced.

        Never touches the backend (like ``healthz``/``robots_txt``): it's a static asset, so a
        guest embedding a widget on their own site never depends on this app's SDK/API round-trip
        just to fetch the loader. ``Cache-Control`` is deliberately LONG
        (``EMBED_JS_CACHE_CONTROL``) since the file changes rarely; ``docs/embedding.md`` tells
        integrators to cache-bust a real update with a ``?v=`` query string on the
        ``<script src>`` rather than relying on this expiring on its own.
        """
        del request
        return FileResponse(
            STATIC_DIR / "embed.js",
            media_type="text/javascript",
            headers={"Cache-Control": EMBED_JS_CACHE_CONTROL},
        )

    def healthz(self, request: Request) -> Response:
        """Liveness only — never calls the API, so it stays up even if the backend is down."""
        del request  # Starlette passes the request; liveness ignores it.
        return PlainTextResponse("ok")

    def catch_all(self, request: Request) -> Response:
        """The branded 404 for any path with no registered route (never Starlette's bare default).

        Registered LAST in ``create_app`` so every specific route still matches first — this only
        catches what nothing else did.
        """
        return _not_found(request, self._locale(request), base_url=self._settings.base_url)


def create_app(
    *,
    settings: BookingSettings,
    client_factory: Callable[[], AetherCalClient],
    rate_limiter: _RateLimiter | None = None,
) -> FastHTML:
    """Build the FastHTML booking app bound to ``settings`` and an SDK ``client_factory``.

    ``rate_limiter`` is an injection seam (tests pass a small-threshold instance); production
    callers can omit it to get the default per-IP limiter.
    """
    booking = _BookingApp(settings, client_factory, rate_limiter=rate_limiter)
    # Order matters: the security-headers middleware is OUTERMOST so its headers are applied even to
    # the rate limiter's early 429. The rate limiter is INNER but still ahead of routing/FastHTML's
    # body parse — so a blocked POST is rejected before its body is read.
    app = FastHTML(
        middleware=[
            Middleware(
                _SecurityHeadersMiddleware,
                embed_allowed_origins=settings.embed_allowed_origins,
                # The captcha's script and its challenge iframe both come from Cloudflare's origin.
                # Forget this and the browser BLOCKS the widget: no token is submitted, and the API
                # — correctly — refuses every booking. A CSP that silently breaks the only gate in
                # front of an unauthenticated write is not a detail.
                turnstile=settings.turnstile_site_key is not None,
            ),
            booking.rate_limit_middleware(),
        ]
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _register(app, "/", booking.index, ["GET"])
    _register(app, "/healthz", booking.healthz, ["GET"])
    _register(app, "/robots.txt", booking.robots_txt, ["GET"])
    # B2.2: a clean-URL alias for the embed widget loader (also reachable, unversioned, at
    # /static/embed.js — kept for both since some integrators reference /static/* directly).
    _register(app, "/embed.js", booking.embed_js, ["GET"])
    _register(app, "/cancel", booking.cancel_form, ["GET"])
    _register(app, "/cancel", booking.cancel_submit, ["POST"])
    _register(app, "/reschedule", booking.reschedule_form, ["GET"])
    _register(app, "/reschedule", booking.reschedule_submit, ["POST"])
    _register(app, "/e/{slug}", booking.event, ["GET"])
    _register(app, "/e/{slug}/slots", booking.slots_partial, ["GET"])
    _register(app, "/e/{slug}/book", booking.book_form, ["GET"])
    _register(app, "/e/{slug}/book", booking.book_submit, ["POST"])
    # /embed/* (B0/B1): the SAME handlers as above, reused verbatim — each derives `embed`/the
    # routing prefix from `request.url.path`, so the compact chrome-less flow needs no separate
    # implementation. Framing is relaxed for this whole prefix by `security_headers` (B0).
    _register(app, "/embed/{slug}", booking.event, ["GET"])
    _register(app, "/embed/{slug}/slots", booking.slots_partial, ["GET"])
    _register(app, "/embed/{slug}/book", booking.book_form, ["GET"])
    _register(app, "/embed/{slug}/book", booking.book_submit, ["POST"])
    # ==THE BUSINESS-SCOPED TWINS — this is what makes ONE page serve N businesses.==
    #
    # The SAME handlers, again, under `/t/{tenant}`. Not a second implementation: each handler asks
    # `self._tenant(request)`, which reads the slug from the path when the route carries one and
    # falls back to `AETHERCAL_TENANT_SLUG` when it does not — so `_booking_prefix` keeps every
    # link,
    # action and redirect a response emits inside whichever space the guest is already in.
    #
    # The unprefixed routes above are NOT legacy: they are the single-business self-hoster, whose
    # URLs (and whose guests' bookmarks and e-mailed links) must not break on the day the page
    # learned to serve more than one.
    _register(app, "/t/{tenant}", booking.index, ["GET"])
    _register(app, "/t/{tenant}/e/{slug}", booking.event, ["GET"])
    _register(app, "/t/{tenant}/e/{slug}/slots", booking.slots_partial, ["GET"])
    _register(app, "/t/{tenant}/e/{slug}/book", booking.book_form, ["GET"])
    _register(app, "/t/{tenant}/e/{slug}/book", booking.book_submit, ["POST"])
    _register(app, "/t/{tenant}/embed/{slug}", booking.event, ["GET"])
    _register(app, "/t/{tenant}/embed/{slug}/slots", booking.slots_partial, ["GET"])
    _register(app, "/t/{tenant}/embed/{slug}/book", booking.book_form, ["GET"])
    _register(app, "/t/{tenant}/embed/{slug}/book", booking.book_submit, ["POST"])
    # Catch-all MUST be registered last: Starlette matches routes in registration order, so every
    # specific path above still wins; only a truly unmatched path falls through to here.
    _register(app, "/{path:path}", booking.catch_all, ["GET"])
    return app


__all__ = [
    "COMMON_TIMEZONES",
    "EMBED_JS_CACHE_CONTROL",
    "STATIC_DIR",
    "create_app",
    "security_headers",
]
