"""The FastHTML application: stateless, SSR + HTMX, wired to the API only through the SDK.

The app never touches the database — it calls the AetherCal API on the guest's behalf with a
**server-side** API key (the D4 rule), so a guest never sees a key. ``create_app`` builds a
:class:`_BookingApp` (which holds the settings and a ``client_factory`` returning a fresh
:class:`AetherCalClient`) and wires its handlers as routes; tests inject an
``httpx.MockTransport``-backed client to run the whole app offline.

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

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fasthtml.common import FastHTML
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from aethercal.booking import views
from aethercal.booking.errors import friendly_api_error, friendly_unexpected
from aethercal.booking.forms import BookingRequest, build_booking, parse_questions
from aethercal.booking.i18n import SUPPORTED_LOCALES, Locale, select_locale, t
from aethercal.booking.settings import BookingSettings
from aethercal.booking.timefmt import format_day_heading, format_time, group_slots, today_in_zone
from aethercal.client import AetherCalAPIError, AetherCalClient
from aethercal.schemas.event_types import EventTypeRead, resolve_title

T = TypeVar("T")

#: Server-side logger for backend-failure observability. The RF-16 trust boundaries degrade the
#: guest experience to a friendly page, but every swallowed failure is logged here (with its
#: traceback) so operators can see a failing backend — the log never reaches the guest.
logger = logging.getLogger(__name__)

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


# --------------------------------------------------------------------------------------
# Security headers (A5.3) — the app owns these outright rather than relying on an edge/CDN
# config (portable, OSS-friendly, and correct even when the app is embedded behind a different
# reverse proxy). ``script-src 'self'`` is strict — no CDN, no inline script — made possible by
# self-hosting htmx (A5.1) and externalizing the timezone-detection script (A5.2).
# --------------------------------------------------------------------------------------

_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_BASE_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=(), browsing-topics=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def security_headers(path: str) -> dict[str, str]:
    """The security headers for a response to ``path`` — every route gets the same conservative
    baseline today. This is the single per-route seam a future ``/embed/*`` route (B0) will use to
    relax ``frame-ancestors``/``X-Frame-Options`` for an allow-listed embedder, without touching
    the middleware wiring itself.
    """
    del path  # no per-route variation yet — the seam future work (B0) will extend.
    return dict(_BASE_SECURITY_HEADERS)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Sets the app-owned security headers (``security_headers``) on every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in security_headers(request.url.path).items():
            response.headers[name] = value
        return response


# --------------------------------------------------------------------------------------
# Pure request/parse helpers (no app state).
# --------------------------------------------------------------------------------------


def _valid_tz(value: str | None) -> str | None:
    if not value:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


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


def _find_event(events: Sequence[EventTypeRead], slug: str) -> EventTypeRead | None:
    return next((e for e in events if e.slug == slug and e.active), None)


def _shifted_url(
    path: str, base: Mapping[str, str], anchor: date, delta_days: int, *, floor: date
) -> str:
    # ``floor`` is the visitor's local "today" — the window never navigates before it. It must be
    # derived from the booking timezone (via ``_today_in``), never the server clock.
    new_from = max(anchor + timedelta(days=delta_days), floor)
    return f"{path}?{urlencode({**base, 'from': new_from.isoformat()})}"


def _not_found(request: Request, locale: Locale) -> Response:
    body = views.message_page(
        locale,
        title=t(locale, "not_found_title"),
        message=t(locale, "not_found_body"),
        lang_urls=_lang_links_here(request),
        is_error=True,
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


def _error_response(
    locale: Locale,
    *,
    title: str,
    exc: Exception | None,
    lang_urls: dict[Locale, str],
    retry: tuple[str, str] | None = None,
) -> Response:
    """A friendly, localized error page with the correct HTTP status — never leaks internals.

    ``retry`` is an optional ``(url, label)`` affordance shown as a button (e.g. "back to times").
    """
    message = (
        friendly_api_error(exc, locale)
        if isinstance(exc, AetherCalAPIError)
        else friendly_unexpected(locale)
    )
    status = _http_status_for(exc)
    if status >= 500 and exc is not None:
        # A backend 5xx, transport drop, or unexpected error — observable to ops, hidden from the
        # guest. A clean client signal (409/403/404) is expected flow, not an error to log.
        logger.error("booking page: backend failure rendering %r", title, exc_info=exc)
    retry_url, retry_label = retry if retry is not None else (None, None)
    body = views.message_page(
        locale,
        title=title,
        message=message,
        lang_urls=lang_urls,
        back_url=retry_url,
        back_label=retry_label,
        is_error=True,
    )
    return HTMLResponse(views.render(body), status_code=status)


def _register(app: FastHTML, path: str, handler: Callable[..., object], methods: list[str]) -> None:
    """Register a route by explicit call (not the ``@`` decorator) so handlers stay typed."""
    app.route(path, methods=methods)(handler)  # pyright: ignore[reportUnknownMemberType]


class _BookingApp:
    """Holds the settings + SDK factory; its methods are the route handlers (bound to state)."""

    def __init__(
        self, settings: BookingSettings, client_factory: Callable[[], AetherCalClient]
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    async def _call(self, call: Callable[[AetherCalClient], T]) -> T:
        """Run a (blocking) SDK call in a threadpool with a fresh client, closing it after."""

        def invoke() -> T:
            with self._client_factory() as client:
                return call(client)

        return await run_in_threadpool(invoke)

    def _locale(self, request: Request, form_lang: str | None = None) -> Locale:
        return select_locale(
            query_lang=form_lang or request.query_params.get("lang"),
            accept_language=request.headers.get("accept-language"),
            default=self._settings.default_locale,
        )

    async def _slots_section(
        self, event: EventTypeRead, tz: str, window_from: date, today: date, locale: Locale
    ) -> object:
        window_to = window_from + timedelta(days=WINDOW_DAYS - 1)
        try:
            result = await self._call(
                lambda c: c.get_slots(event.id, window_from=window_from, window_to=window_to, tz=tz)
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
            book_path=f"/e/{event.slug}/book",
            prev_url=_shifted_url(f"/e/{event.slug}", base, window_from, -WINDOW_DAYS, floor=today),
            next_url=_shifted_url(f"/e/{event.slug}", base, window_from, WINDOW_DAYS, floor=today),
        )

    async def _events(self) -> list[EventTypeRead] | None:
        """Load the tenant's event types, or ``None`` if the backend can't be reached (RF-16).

        A public-page trust boundary: an API error, a dropped connection, or a malformed response
        must degrade to a friendly page — never a 500/stack. Every SDK failure collapses to
        ``None`` here, and the caller renders the service-unavailable page.
        """
        try:
            return await self._call(lambda c: c.list_event_types())
        except Exception:
            logger.exception("booking page: failed to load event types")
            return None

    def _service_error(
        self, locale: Locale, *, lang_urls: dict[Locale, str], retry_url: str
    ) -> Response:
        """The friendly 'service temporarily unavailable' page (503) with a retry affordance."""
        body = views.message_page(
            locale,
            title=t(locale, "app_name"),
            message=t(locale, "error_generic"),
            lang_urls=lang_urls,
            back_url=retry_url,
            back_label=t(locale, "retry"),
            is_error=True,
        )
        return HTMLResponse(views.render(body), status_code=503)

    # -- routes -------------------------------------------------------------------------

    async def index(self, request: Request) -> object:
        locale = self._locale(request)
        events = await self._events()
        if events is None:
            return self._service_error(
                locale, lang_urls=_lang_links_here(request), retry_url=str(request.url)
            )
        active = [event for event in events if event.active]
        return views.index_page(locale, event_types=active, lang_urls=_lang_links_here(request))

    async def event(self, request: Request) -> object:
        locale = self._locale(request)
        slug = str(request.path_params["slug"])
        tz, tz_explicit = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        events = await self._events()
        if events is None:
            return self._service_error(
                locale, lang_urls=_lang_links_here(request), retry_url=str(request.url)
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale)
        section = await self._slots_section(found, tz, window_from, today, locale)
        return views.event_page(
            locale,
            event=found,
            tz=tz,
            tz_options=COMMON_TIMEZONES,
            tz_explicit=tz_explicit,
            window_from=window_from.isoformat(),
            slots=section,
            self_path=f"/e/{slug}",
            slots_endpoint=f"/e/{slug}/slots",
            lang_urls=_lang_links_here(request),
        )

    async def slots_partial(self, request: Request) -> object:
        slug = str(request.path_params["slug"])
        if request.headers.get("HX-Request") is None:
            query = request.url.query
            return RedirectResponse(
                f"/e/{slug}?{query}" if query else f"/e/{slug}", status_code=303
            )
        locale = self._locale(request)
        tz, _ = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        events = await self._events()
        if events is None:
            # HTMX swaps only on 2xx: degrade the fragment in place, not with a non-swapping 5xx.
            return views.slots_unavailable_fragment(locale)
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale)
        return await self._slots_section(found, tz, window_from, today, locale)

    async def book_form(self, request: Request) -> object:
        locale = self._locale(request)
        slug = str(request.path_params["slug"])
        tz, _ = _tz_of(request)
        start = request.query_params.get("start", "")
        events = await self._events()
        if events is None:
            return self._service_error(
                locale, lang_urls=_lang_links_here(request), retry_url=str(request.url)
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale)
        instant = _parse_instant(start)
        if instant is None:
            return RedirectResponse(
                f"/e/{slug}?{urlencode({'tz': tz, 'lang': locale})}", status_code=303
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
            action=f"/e/{slug}/book",
            lang_urls=_lang_links(f"/e/{slug}/book", {"start": start, "tz": tz}),
        )

    async def book_submit(self, request: Request) -> object:
        form = _form_dict(await request.form())
        locale = self._locale(request, form.get("lang"))
        slug = str(request.path_params["slug"])
        tz = _valid_tz(form.get("tz")) or DEFAULT_TZ
        start = form.get("start", "")
        events = await self._events()
        if events is None:
            return self._service_error(
                locale,
                lang_urls=_lang_links(f"/e/{slug}/book", {"start": start, "tz": tz}),
                retry_url=f"/e/{slug}?{urlencode({'tz': tz, 'lang': locale})}",
            )
        found = _find_event(events, slug)
        if found is None:
            return _not_found(request, locale)
        questions = parse_questions(found.questions)
        instant = _parse_instant(start)
        label = _when_label(instant, tz, locale) if instant is not None else ""
        lang_urls = _lang_links(f"/e/{slug}/book", {"start": start, "tz": tz})
        booking_request = BookingRequest(
            event_type_id=found.id, start_iso=start, guest_timezone=tz, locale=locale
        )
        result = build_booking(booking_request, questions=questions, form=form)
        booking_create = result.booking
        if booking_create is None:
            return views.booking_form_page(
                locale,
                event=found,
                start_iso=start,
                tz=tz,
                when_label=label,
                questions=questions,
                values=result.values,
                errors=result.errors,
                action=f"/e/{slug}/book",
                lang_urls=lang_urls,
            )
        try:
            booking = await self._call(lambda c: c.create_booking(booking_create))
        except Exception as exc:
            back = f"/e/{slug}?{urlencode({'tz': tz, 'lang': locale})}"
            return _error_response(
                locale,
                title=resolve_title(found, locale),
                exc=exc,
                lang_urls=lang_urls,
                retry=(back, t(locale, "back_to_times")),
            )
        return views.confirmation_page(
            locale,
            event=found,
            booking=booking,
            when_label=label,
            lang_urls=_lang_links_here(request),
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
                is_error=True,
            )
        return views.cancel_confirm_page(
            locale,
            booking_id=booking_id,
            token=token,
            action="/cancel",
            lang_urls=_lang_links_here(request),
        )

    async def cancel_submit(self, request: Request) -> object:
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
                is_error=True,
            )
        try:
            await self._call(lambda c: c.cancel_booking(booking_id, token=token))
        except Exception as exc:
            return _error_response(
                locale, title=t(locale, "cancel_title"), exc=exc, lang_urls=lang_urls
            )
        return views.message_page(
            locale,
            title=t(locale, "cancel_title"),
            message=t(locale, "cancel_done"),
            lang_urls=lang_urls,
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
                is_error=True,
            )
        tz, tz_explicit = _tz_of(request)
        today = _today_in(tz)
        window_from = _window_of(request, today)
        window_to = window_from + timedelta(days=WINDOW_DAYS - 1)
        try:
            result = await self._call(
                lambda c: c.get_slots(event_id, window_from=window_from, window_to=window_to, tz=tz)
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
        )

    async def reschedule_submit(self, request: Request) -> object:
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
                is_error=True,
            )
        try:
            await self._call(
                lambda c: c.reschedule_booking(booking_id, new_start=new_start, token=token)
            )
        except Exception as exc:
            return _error_response(
                locale, title=t(locale, "reschedule_title"), exc=exc, lang_urls=lang_urls
            )
        return views.message_page(
            locale,
            title=t(locale, "reschedule_title"),
            message=t(locale, "reschedule_done"),
            lang_urls=lang_urls,
        )

    def healthz(self, request: Request) -> Response:
        """Liveness only — never calls the API, so it stays up even if the backend is down."""
        del request  # Starlette passes the request; liveness ignores it.
        return PlainTextResponse("ok")


def create_app(
    *,
    settings: BookingSettings,
    client_factory: Callable[[], AetherCalClient],
) -> FastHTML:
    """Build the FastHTML booking app bound to ``settings`` and an SDK ``client_factory``."""
    booking = _BookingApp(settings, client_factory)
    app = FastHTML(middleware=[Middleware(_SecurityHeadersMiddleware)])
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _register(app, "/", booking.index, ["GET"])
    _register(app, "/healthz", booking.healthz, ["GET"])
    _register(app, "/cancel", booking.cancel_form, ["GET"])
    _register(app, "/cancel", booking.cancel_submit, ["POST"])
    _register(app, "/reschedule", booking.reschedule_form, ["GET"])
    _register(app, "/reschedule", booking.reschedule_submit, ["POST"])
    _register(app, "/e/{slug}", booking.event, ["GET"])
    _register(app, "/e/{slug}/slots", booking.slots_partial, ["GET"])
    _register(app, "/e/{slug}/book", booking.book_form, ["GET"])
    _register(app, "/e/{slug}/book", booking.book_submit, ["POST"])
    return app


__all__ = ["COMMON_TIMEZONES", "STATIC_DIR", "create_app", "security_headers"]
