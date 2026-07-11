"""Reflex state for the admin (F1-11, RF-18).

A single :class:`AdminState` holds the whole admin session: the login flag plus the three data
lists. Keeping it in ONE state is a security decision, not just convenience — authorization must be
checked *inside every event handler* (a page ``on_load`` guard alone does not protect a handler a
client can invoke directly over the websocket), and a single state lets every handler consult the
same ``_authenticated`` flag with no cross-state plumbing.

Two hardening choices back that up:

* ``_authenticated`` is a **backend-only var** (leading underscore): Reflex never ships it to the
  frontend and generates no client-callable setter, so a client cannot flip it — only the ``login``
  handler (which verifies the PBKDF2 hash) sets it.
* every handler except ``login``/``logout``/``require_auth`` returns immediately unless
  ``_authenticated`` is set, so no unauthenticated event can read or mutate tenant data.

The handlers stay thin glue: they delegate to the in-process :mod:`aethercal.server.admin.service`
layer (which owns the DB transaction and tenant scoping) and surface any :class:`AdminError` as a
safe ``error`` string. The DB/business logic — and its tests — live in ``service``/``format``. The
fetch helpers are module-level async functions (not state methods) on purpose, so a plain "reload"
helper is not turned into an event handler by Reflex.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import reflex as rx
from reflex.event import EventSpec
from sqlalchemy.exc import SQLAlchemyError

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleUpdate
from aethercal.server.admin import service
from aethercal.server.admin.auth import authenticate
from aethercal.server.admin.format import (
    booking_event,
    booking_row,
    event_type_row,
    parse_weekdays,
    schedule_row,
    weekly_rules,
)
from aethercal.server.admin.ratelimit import LOGIN_LIMITER, PBKDF2_LIMITER, LoginThrottledError
from aethercal.server.admin.runtime import AdminRuntime, current_runtime
from aethercal.ui import CalendarEvent

# Internal (prefix-free) routes; the mount prefix is applied by Reflex's ``frontend_path`` basename.
LOGIN_ROUTE = "/login"
HOME_ROUTE = "/"

_LOCKED_OUT_MESSAGE = "Too many attempts. Please wait and try again."


def _rate_keys(state: AdminState, username: str) -> list[str]:
    """The rate-limit keys for a login attempt: the client IP and (if present) the username.

    Keying by BOTH means a brute-force from one IP (many usernames) and one username (many IPs) both
    trip the limiter, and — crucially — the budget is per-IP/per-username at the PROCESS level, so
    opening a fresh session does not reset it.
    """
    ip = state.router.session.client_ip or "unknown"
    keys = [f"ip:{ip}"]
    if username:
        keys.append(f"user:{username}")
    return keys


def _error_text(exc: Exception) -> str:
    """Render any admin/parse failure as a safe, operator-facing message."""
    if isinstance(exc, service.AdminActionError):
        return exc.message
    if isinstance(exc, service.AdminSetupError):
        return str(exc)
    if isinstance(exc, ValueError):
        return f"Invalid input: {exc}"
    return "Something went wrong."  # pragma: no cover - defensive


def _clean(form_data: dict[str, str], key: str) -> str:
    """A stripped string value from a submitted form (missing → empty)."""
    return str(form_data.get(key, "")).strip()


def _en_translation(form_data: dict[str, str], key: str) -> dict[str, str]:
    """A sparse ``{"en": value}`` translation map from a form field; blank → ``{}`` (A4).

    Blank never persists an ``"en"`` key at all — an empty override is not a meaningful override
    (mirrors :func:`aethercal.schemas.event_types.resolve_title`'s own "blank falls back" rule).
    """
    value = _clean(form_data, key)
    return {"en": value} if value else {}


#: Values a checkbox form field carries when checked (radix Checkbox submits ``"on"``; other truthy
#: spellings are accepted defensively). Unchecked omits the field entirely → not in this set.
_CHECKED_VALUES = frozenset({"on", "true", "1", "yes"})


def _is_checked(form_data: dict[str, str], key: str) -> bool:
    """Whether a checkbox form field is checked (present with a truthy value)."""
    return _clean(form_data, key).lower() in _CHECKED_VALUES


def _translation_update(
    form_data: dict[str, str], *, value_key: str, clear_key: str
) -> dict[str, str] | None:
    """The translation-map update for one EN field on the UPDATE form, or ``None`` to leave the
    stored map UNTOUCHED.

    Removal is EXPLICIT, never implicit: only the ``clear_key`` checkbox empties a translation
    (``{}``), and it wins over any typed value. A new non-blank value sets ``{"en": value}``. A
    blank field with the checkbox unchecked returns ``None`` → the field is omitted from the update
    payload, PRESERVING the existing override — so editing another field (e.g. duration) can never
    silently drop a saved translation.
    """
    if _is_checked(form_data, clear_key):
        return {}
    value = _clean(form_data, value_key)
    return {"en": value} if value else None


# --------------------------------------------------------------------------------------
# Fetch helpers (free functions the handlers await).
# --------------------------------------------------------------------------------------


#: The calendar surfaces the operator can switch between (kept in sync with the TS ``CalendarView``
#: union and the Reflex wrapper's ``_VALID_VIEWS``). ``list`` is the agenda-list fallback.
_VALID_CALENDAR_VIEWS = frozenset({"month", "week", "day", "list"})

#: Shown when a resize would only change a booking's DURATION (its start is unchanged). A booking's
#: end is derived from its event type, so duration can't be edited — the operator drags to MOVE it.
_DURATION_FIXED_MESSAGE = (
    "La duración de una reserva la determina su tipo de evento; "
    "arrastra la reserva para moverla en el tiempo."
)

#: Shown when a reschedule PERSISTED but the follow-up refresh failed. The move is real (kept in the
#: view); only the authoritative sync is pending, so the operator is asked to reload — never a
#: rollback (which would desync the view from the committed DB state).
_RESYNC_MESSAGE = (
    "La reserva se reprogramó, pero no se pudo actualizar la vista. Recarga la agenda."
)


async def _fetch_booking_views(
    runtime: AdminRuntime,
) -> tuple[list[dict[str, str]], list[CalendarEvent]]:
    """Load the tenant's bookings once, projected into BOTH the table rows and the calendar events.

    Cancelled bookings are omitted from the calendar (a reschedule cancels the predecessor, so
    showing it would duplicate the moved chip), while the row list keeps the full history.
    """
    reads: list[BookingRead] = await service.list_bookings_view(
        runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug
    )
    rows = [booking_row(read) for read in reads]
    events = [booking_event(read) for read in reads if read.status is not BookingStatus.CANCELLED]
    return rows, events


def _find_calendar_event(events: list[CalendarEvent], booking_id: str) -> CalendarEvent | None:
    """A shallow copy of the event with ``booking_id`` (a rollback snapshot), or ``None``."""
    for event in events:
        if event["id"] == booking_id:
            return dict(event)  # type: ignore[return-value]  # a shallow snapshot for rollback
    return None


def _with_moved_event(
    events: list[CalendarEvent], booking_id: str, start: str, end: str
) -> list[CalendarEvent]:
    """The event list with ``booking_id`` moved to ``start``/``end`` (the optimistic apply)."""
    return [
        ({**event, "start": start, "end": end} if event["id"] == booking_id else event)
        for event in events
    ]


def _with_restored_event(
    events: list[CalendarEvent], snapshot: CalendarEvent
) -> list[CalendarEvent]:
    """The event list with the snapshotted event put back in place (the rollback)."""
    return [(snapshot if event["id"] == snapshot["id"] else event) for event in events]


def _parse_admin_start(raw: str) -> datetime:
    """Parse an ISO datetime from the admin/calendar; a naive value is stamped UTC (admin contract).

    The calendar echoes naive local wall-time and the ``datetime-local`` field is naive; both are
    interpreted as UTC (the inverse of :func:`aethercal.server.admin.format._wall_time_utc`). An
    already-aware value is honored as sent.
    """
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def _same_instant(a: str, b: str) -> bool:
    """Whether two admin/calendar datetime strings denote the same instant (format-tolerant)."""
    try:
        return _parse_admin_start(a) == _parse_admin_start(b)
    except ValueError:
        return a == b


async def _optimistic_reschedule(
    state: AdminState, payload: dict[str, str], *, require_start_change: bool = False
) -> AsyncIterator[None]:
    """Optimistic reschedule (RF-21) shared by drag and resize.

    Applies the move to ``calendar_events`` IMMEDIATELY and ``yield``s so the client sees the chip
    in its new slot with no lag; then calls the real ``reschedule_booking_action``. On success it
    COMMITS the server's confirmed state (a refetch — the reschedule opens a new confirmed booking
    and cancels the old, so the authoritative list is the source of truth for the new revision); on
    a refusal (off-hours, taken slot, ...) it ROLLS BACK to the snapshot and surfaces the error.

    ``require_start_change`` guards the resize gesture: a booking's duration is derived from its
    event type, so a resize that leaves the START unchanged (only the end/duration edited) has no
    valid server operation — it is refused up front with a clear message and NO service call,
    instead of opening a pointless same-start successor whose end would just snap back. A resize
    that moves the start reschedules exactly like a drag (the duration re-derives).

    The caller (the public drag/resize handlers) owns the auth guard; this reads only public state.
    """
    booking_id = str(payload.get("id", ""))
    snapshot = _find_calendar_event(state.calendar_events, booking_id)
    if snapshot is None:
        return  # a gesture on an event the server no longer has: nothing to move
    new_start_raw = str(payload.get("start", ""))
    if require_start_change and _same_instant(new_start_raw, str(snapshot["start"])):
        # A pure duration change (start unchanged): not editable, and never a DB round-trip.
        state.error = _DURATION_FIXED_MESSAGE
        return
    # Optimistic apply: move the chip now, before any DB round-trip.
    state.calendar_events = _with_moved_event(
        state.calendar_events, booking_id, new_start_raw, str(payload.get("end", ""))
    )
    state.error = ""
    yield
    runtime = current_runtime()
    committed = False
    try:
        new_start = _parse_admin_start(new_start_raw)
        await service.reschedule_booking_action(
            runtime.sessionmaker,
            tenant_slug=runtime.config.tenant_slug,
            booking_id=uuid.UUID(booking_id),
            new_start=new_start,
        )
        committed = True
        # The reschedule replaced the booking with a new-id successor, so a manage panel open on the
        # old (now cancelled) id is stale — clear it, like reschedule / reschedule_selected do.
        state.selected_booking_id = ""
        # Commit the authoritative state (the confirmed successor at its new revision).
        state.bookings, state.calendar_events = await _fetch_booking_views(runtime)
        state.error = ""
    except (ValueError, service.AdminError, SQLAlchemyError) as exc:
        if committed:
            # The reschedule PERSISTED but the refresh failed. Rolling back would desync the view
            # from the DB (which really moved), so KEEP the optimistic move and ask for a reload —
            # the next load reconciles to the authoritative revision.
            state.error = _RESYNC_MESSAGE
        else:
            # Refused or failed BEFORE commit → roll the chip back to the authoritative slot. Every
            # exit reconciles the optimistic state: no path leaves an unconfirmed move applied.
            state.calendar_events = _with_restored_event(state.calendar_events, snapshot)
            state.error = _error_text(exc)


async def _fetch_event_types(runtime: AdminRuntime) -> list[dict[str, str]]:
    rows = await service.list_event_types_view(
        runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug
    )
    return [event_type_row(row) for row in rows]


async def _fetch_schedules(runtime: AdminRuntime) -> list[dict[str, str]]:
    rows = await service.list_schedules_view(
        runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug
    )
    return [schedule_row(row) for row in rows]


def _schedule_id(schedules: list[dict[str, str]], name: str) -> uuid.UUID:
    """Resolve a schedule id by its (tenant-unique) name from the loaded schedule rows."""
    for row in schedules:
        if row["name"] == name:
            return uuid.UUID(row["id"])
    raise ValueError(f"unknown schedule {name!r}")


class AdminState(rx.State):
    """The whole admin session: the login flag (backend-only) + the three data lists."""

    # Security-critical: backend-only var — never sent to the frontend, no client setter (RF-18).
    _authenticated: bool = False
    error: str = ""
    bookings: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    event_types: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    schedules: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)

    # -- calendar (F2-F) ------------------------------------------------------------
    # The agenda's calendar events (active bookings) + the operator-selected view. The calendar
    # component reads both; ``calendar_events`` also carries the optimistic in-flight move.
    calendar_events: list[CalendarEvent] = []  # noqa: RUF012 (reflex state var)
    calendar_view: str = "month"
    # The booking selected by a click, surfaced in the manage panel (view / cancel / reschedule).
    selected_booking_id: str = ""
    selected_booking_start: str = ""
    selected_booking_guest: str = ""
    # The range-select create affordance: the pre-filled start + whether the create panel is open.
    new_booking_start: str = ""
    show_new_booking: bool = False

    # -- auth -----------------------------------------------------------------------

    @rx.event
    async def login(self, form_data: dict[str, str]) -> EventSpec | None:
        """Verify the submitted credentials against the env config; redirect home on success.

        Rate-limited at the PROCESS level by client IP + username (a new session does not reset the
        budget) and verified off the event loop under a bounded-concurrency semaphore, so the slow
        PBKDF2 never blocks the loop and a login flood cannot exhaust CPU. Messages are generic
        (RF-16); the primary brute-force defense remains the reverse proxy (see ``ratelimit``).
        """
        username = _clean(form_data, "username")
        keys = _rate_keys(self, username)
        if LOGIN_LIMITER.any_locked(keys):
            self.error = _LOCKED_OUT_MESSAGE
            return None
        runtime = current_runtime()
        password = str(form_data.get("password", ""))
        try:
            async with PBKDF2_LIMITER.slot():
                # Re-check the lockout INSIDE the acquired slot, just before spending a derivation:
                # once concurrent failures trip the lock, queued attempts abort here rather than
                # racing past the pre-check (bounds the overshoot to the concurrency limit, RF-18).
                if LOGIN_LIMITER.any_locked(keys):
                    self.error = _LOCKED_OUT_MESSAGE
                    return None
                authenticated = await asyncio.to_thread(
                    authenticate, runtime.config, username, password
                )
        except LoginThrottledError:
            self.error = _LOCKED_OUT_MESSAGE
            return None
        if authenticated:
            self._authenticated = True
            for key in keys:
                LOGIN_LIMITER.record_success(key)
            self.error = ""
            return rx.redirect(HOME_ROUTE)
        self._authenticated = False
        for key in keys:
            LOGIN_LIMITER.record_failure(key)
        self.error = (
            _LOCKED_OUT_MESSAGE
            if LOGIN_LIMITER.any_locked(keys)
            else ("Invalid username or password.")
        )
        return None

    @rx.event
    def logout(self) -> EventSpec:
        """Clear the session and return to the login page."""
        self._authenticated = False
        return rx.redirect(LOGIN_ROUTE)

    @rx.event
    def require_auth(self) -> EventSpec | None:
        """``on_load`` guard for every protected page: bounce to login when not authenticated."""
        if not self._authenticated:
            return rx.redirect(LOGIN_ROUTE)
        return None

    # -- bookings -------------------------------------------------------------------

    @rx.event
    async def load_bookings(self) -> None:
        """Load the tenant's bookings into the agenda calendar + row list (``on_load``)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.bookings, self.calendar_events = await _fetch_booking_views(current_runtime())
        except service.AdminError as exc:
            self.error = _error_text(exc)

    @rx.event
    async def cancel(self, booking_id: str) -> None:
        """Cancel the booking with ``booking_id`` and refresh the agenda."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.cancel_booking_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                booking_id=uuid.UUID(booking_id),
            )
            self.bookings, self.calendar_events = await _fetch_booking_views(runtime)
            self.selected_booking_id = ""
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def reschedule(self, form_data: dict[str, str]) -> None:
        """Reschedule a booking to a new start (the manual, keyboard-friendly fallback form).

        The ``datetime-local`` field is timezone-naive; the admin's contract is that its times are
        UTC, so a naive value is stamped as UTC explicitly here (rather than relying on a downstream
        default) — an aware value is honored as sent.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            booking_id = uuid.UUID(_clean(form_data, "booking_id"))
            new_start = _parse_admin_start(_clean(form_data, "new_start"))
            await service.reschedule_booking_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                booking_id=booking_id,
                new_start=new_start,
            )
            self.bookings, self.calendar_events = await _fetch_booking_views(runtime)
            self.selected_booking_id = ""
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    # -- calendar interactions (F2-F) -----------------------------------------------

    @rx.event
    def set_calendar_view(self, view: str) -> None:
        """Switch the calendar surface (month / week / day / list) from the view toggle."""
        if not self._authenticated:
            return
        if view in _VALID_CALENDAR_VIEWS:
            self.calendar_view = view

    @rx.event
    async def on_calendar_event_drop(self, payload: dict[str, str]) -> AsyncIterator[None]:
        """Drag an event onto a new day/time → reschedule, optimistically (RF-21)."""
        if not self._authenticated:
            return
        async for _ in _optimistic_reschedule(self, payload):
            yield

    @rx.event
    async def on_calendar_event_resize(self, payload: dict[str, str]) -> AsyncIterator[None]:
        """Resize an event's edge → reschedule to the new start (duration stays event-type-derived).

        A booking's ``end`` is derived from its event type, so a resize that only changes duration
        (start unchanged) is refused with a clear message and no service call; a resize that moves
        the start reschedules exactly like a drag (same optimistic apply → commit / rollback path).
        """
        if not self._authenticated:
            return
        async for _ in _optimistic_reschedule(self, payload, require_start_change=True):
            yield

    @rx.event
    def on_calendar_range_select(self, payload: dict[str, str]) -> None:
        """Drag across empty grid space → open the create-booking panel (pre-filled start)."""
        if not self._authenticated:
            return
        self.new_booking_start = str(payload.get("start", ""))
        self.show_new_booking = True
        self.selected_booking_id = ""
        self.error = ""

    @rx.event
    def on_calendar_event_click(self, payload: dict[str, str]) -> None:
        """Click an event → select it for the manage panel (view / cancel / reschedule)."""
        if not self._authenticated:
            return
        booking_id = str(payload.get("id", ""))
        for event in self.calendar_events:
            if event["id"] == booking_id:
                self.selected_booking_id = booking_id
                self.selected_booking_start = str(event["start"])
                self.selected_booking_guest = str(event["title"])
                self.show_new_booking = False
                self.error = ""
                return

    @rx.event
    async def reschedule_selected(self, form_data: dict[str, str]) -> None:
        """Reschedule the CLICKED booking to a new start (the accessible panel form).

        Uses ``selected_booking_id`` from state, so the operator never needs to know/type a booking
        id — clicking the event on the calendar selects it. This is the keyboard-accessible path
        that does not rely on drag.
        """
        if not self._authenticated or not self.selected_booking_id:
            return
        runtime = current_runtime()
        committed = False
        try:
            new_start = _parse_admin_start(_clean(form_data, "new_start"))
            await service.reschedule_booking_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                booking_id=uuid.UUID(self.selected_booking_id),
                new_start=new_start,
            )
            committed = True
            self.bookings, self.calendar_events = await _fetch_booking_views(runtime)
            self.selected_booking_id = ""
            self.error = ""
        except (ValueError, service.AdminError, SQLAlchemyError) as exc:
            if committed:
                # Rescheduled, but the refresh failed: close the now-stale panel and ask for a
                # reload instead of leaving it pointed at the replaced booking (ambiguous retry).
                self.selected_booking_id = ""
                self.error = _RESYNC_MESSAGE
            else:
                self.error = _error_text(exc)

    @rx.event
    def clear_selection(self) -> None:
        """Close the manage panel."""
        self.selected_booking_id = ""

    @rx.event
    def close_new_booking(self) -> None:
        """Close the create-booking panel without creating."""
        self.show_new_booking = False
        self.new_booking_start = ""

    @rx.event
    async def create_booking(self, form_data: dict[str, str]) -> None:
        """Create a booking for the range-selected slot (reuses the domain booking service)."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        committed = False
        try:
            form = service.BookingForm(
                event_type_id=uuid.UUID(_clean(form_data, "event_type_id")),
                start=_parse_admin_start(_clean(form_data, "start")),
                guest_name=_clean(form_data, "guest_name"),
                guest_email=_clean(form_data, "guest_email"),
                guest_timezone=_clean(form_data, "guest_timezone") or "UTC",
            )
            await service.create_booking_action(
                runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug, form=form
            )
            committed = True
            self.bookings, self.calendar_events = await _fetch_booking_views(runtime)
            self.show_new_booking = False
            self.new_booking_start = ""
            self.error = ""
        except (ValueError, service.AdminError, SQLAlchemyError) as exc:
            if committed:
                # Created, but the refresh failed: close the form so the operator does not re-submit
                # (an ambiguous retry) — the booking persisted; ask for a reload.
                self.show_new_booking = False
                self.new_booking_start = ""
                self.error = _RESYNC_MESSAGE
            else:
                self.error = _error_text(exc)

    # -- event types ----------------------------------------------------------------

    @rx.event
    async def load_event_types(self) -> None:
        """Load event types + schedules (the create form needs the schedule choices)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            runtime = current_runtime()
            self.event_types = await _fetch_event_types(runtime)
            self.schedules = await _fetch_schedules(runtime)
        except service.AdminError as exc:
            self.error = _error_text(exc)

    @rx.event
    async def create_event_type(self, form_data: dict[str, str]) -> None:
        """Create an event type from the submitted form."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            form = service.EventTypeForm(
                slug=_clean(form_data, "slug"),
                title=_clean(form_data, "title"),
                schedule_id=_schedule_id(self.schedules, _clean(form_data, "schedule")),
                duration_seconds=int(_clean(form_data, "duration_min")) * 60,
                max_advance_seconds=int(_clean(form_data, "max_advance_days")) * 86_400,
                title_translations=_en_translation(form_data, "title_en"),
                description_translations=_en_translation(form_data, "description_en"),
            )
            await service.create_event_type_action(
                runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug, form=form
            )
            self.event_types = await _fetch_event_types(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_event_type(self, form_data: dict[str, str]) -> None:
        """Update an event type's title/duration/EN translations.

        CANONICAL fields (``title``, ``duration``) are OMITTED from the payload when blank, never
        sent as an explicit ``None`` — ``title`` is NOT NULL in the DB, so a blank-means-``None``
        payload would flush as a constraint violation instead of a no-op (the bug this fixed while
        wiring the EN translations through, A4).

        TRANSLATION fields (EN) DEFAULT TO PRESERVE: a blank EN field leaves the stored override
        untouched, so editing another field (e.g. duration) never silently drops a saved
        translation. A new value SETS it; removal is EXPLICIT via a per-field "clear" checkbox
        (``clear_title_en`` / ``clear_description_en``) → ``{}`` (see :func:`_translation_update`).
        ``EventTypeUpdate.model_validate`` marks only the keys present in ``update_fields`` as
        "set", matching the ``exclude_unset`` contract the service relies on.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            update_fields: dict[str, str | int | dict[str, str]] = {}
            title = _clean(form_data, "title")
            if title:
                update_fields["title"] = title
            duration_raw = _clean(form_data, "duration_min")
            if duration_raw:
                update_fields["duration_seconds"] = int(duration_raw) * 60
            title_tr = _translation_update(
                form_data, value_key="title_en", clear_key="clear_title_en"
            )
            if title_tr is not None:
                update_fields["title_translations"] = title_tr
            description_tr = _translation_update(
                form_data, value_key="description_en", clear_key="clear_description_en"
            )
            if description_tr is not None:
                update_fields["description_translations"] = description_tr
            data = EventTypeUpdate.model_validate(update_fields)
            await service.update_event_type_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                event_type_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.event_types = await _fetch_event_types(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def deactivate_event_type(self, event_type_id: str) -> None:
        """Soft-delete an event type and refresh the list."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            existed = await service.deactivate_event_type_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                event_type_id=uuid.UUID(event_type_id),
            )
            self.event_types = await _fetch_event_types(runtime)
            # Do not report success for a no-op: an unknown id must surface as an error, not
            # silently "succeed" (the service returns False rather than raising for an absent row).
            self.error = "" if existed else "Event type not found"
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    # -- schedules ------------------------------------------------------------------

    @rx.event
    async def load_schedules(self) -> None:
        """Load the tenant's schedules (``on_load``)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.schedules = await _fetch_schedules(current_runtime())
        except service.AdminError as exc:
            self.error = _error_text(exc)

    @rx.event
    async def create_schedule(self, form_data: dict[str, str]) -> None:
        """Create a weekly schedule; weekdays are a CSV of 0(Mon)..6(Sun)."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            data = ScheduleCreate(
                name=_clean(form_data, "name"),
                timezone=_clean(form_data, "timezone"),
                rules=weekly_rules(
                    parse_weekdays(_clean(form_data, "weekdays")),
                    _clean(form_data, "start"),
                    _clean(form_data, "end"),
                ),
            )
            await service.create_schedule_action(
                runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug, data=data
            )
            self.schedules = await _fetch_schedules(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_schedule(self, form_data: dict[str, str]) -> None:
        """Update a schedule's name/timezone (blank fields left unchanged)."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            data = ScheduleUpdate(
                name=_clean(form_data, "name") or None,
                timezone=_clean(form_data, "timezone") or None,
            )
            await service.update_schedule_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                schedule_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.schedules = await _fetch_schedules(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def delete_schedule(self, schedule_id: str) -> None:
        """Delete a schedule and refresh the list."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.delete_schedule_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                schedule_id=uuid.UUID(schedule_id),
            )
            self.schedules = await _fetch_schedules(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)


__all__ = ["HOME_ROUTE", "LOGIN_ROUTE", "AdminState"]
