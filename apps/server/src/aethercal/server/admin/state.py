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
import enum
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import reflex as rx
from reflex.event import EventSpec
from sqlalchemy.exc import SQLAlchemyError

from aethercal.core.model import BookingStatus, MemberRole
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleUpdate
from aethercal.schemas.workflows import (
    WorkflowCreate,
    WorkflowStepIn,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
)
from aethercal.server.admin import bootstrap, service
from aethercal.server.admin.auth import authenticate
from aethercal.server.admin.format import (
    ALL_EVENT_TYPES,
    SHARED_SCHEDULE,
    booking_event,
    booking_row,
    connection_row,
    event_type_row,
    host_resource,
    host_row,
    member_row,
    metrics_rows,
    parse_weekdays,
    schedule_row,
    template_row,
    weekly_rules,
    workflow_row,
)
from aethercal.server.admin.ratelimit import LOGIN_LIMITER, PBKDF2_LIMITER, LoginThrottledError
from aethercal.server.admin.runtime import AdminRuntime, current_runtime
from aethercal.server.services import rbac
from aethercal.server.services.rbac import Principal, PrincipalKind
from aethercal.ui import CalendarEvent, CalendarResource

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
#: union and the Reflex wrapper's ``_VALID_VIEWS``). ``list`` is the agenda-list fallback, and
#: ``timeline`` is the RF-28 resource view (hosts in rows, time on the horizontal axis).
#:
#: A view missing from this set is IGNORED by the switcher, in silence — so a timeline the component
#: renders perfectly and the state declines to select would ship, do nothing, and fail no test.
_VALID_CALENDAR_VIEWS = frozenset({"month", "week", "day", "list", "timeline"})

#: Why a booking cannot be dragged from one host's row to another (RF-28).
#:
#: ==The component offers the gesture; the backend has no operation for it, and that is a semantics
#: nobody has designed — not an oversight to paper over.== ``bookings`` carries no ``host_id``: the
#: host lives on the EVENT TYPE. So "move this booking to Bruno" means "give this booking a
#: DIFFERENT event type" — another duration, another slug, another public page. Every
#: available guess is wrong in a way the operator would not see, so the gesture is refused with its
#: reason and the appointment is left exactly where it was.
_CROSS_HOST_DRAG_MESSAGE = (
    "Una reserva no se puede mover a otro anfitrión: el anfitrión lo determina el tipo de evento, "
    "así que cambiarlo significaría cambiarle el tipo de cita (otra duración, otro enlace). "
    "Reprograma la reserva en la fila de su anfitrión, o cancélala y crea la nueva."
)

#: The month view renders a FIXED 6-week (42-day) grid (``getMonthGridDays`` + ``_calendar()``'s
#: ``first_day_of_week=1``), so its cells can show up to ~2 weeks of the adjacent months — a 28-day
#: February that begins on the week's last day shows 8 trailing days, which a fixed 6/7-day margin
#: would miss. The data-load window is therefore the EXACT grid extent (computed below), the
#: deliberate "period range vs. data range" split: the emitted period stays re-anchorable while the
#: load covers every day the grid actually renders.
_MONTH_GRID_DAYS = 42

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


async def _host_of_event_type(caller: _Caller) -> dict[uuid.UUID, str]:
    """``event_type_id → host_id``: the map that puts a booking's chip on a timeline row (RF-28).

    It has to be a lookup, and that is the whole point of this requirement: a booking does NOT carry
    a host. It carries an event type, and the event type carries the host. Which is exactly why
    dragging a booking to another host's row has no meaning to give it.
    """
    rows = await service.list_event_types_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return {row.id: str(row.host_id) for row in rows}


@dataclass(frozen=True, slots=True)
class _Caller:
    """WHO is asking, in WHICH business, through which runtime — the three the service layer needs.

    ==The fetch helpers used to take the runtime alone==, and derive the business from
    ``runtime.config.tenant_slug`` — which was the only possible answer while the instance operator
    was the only person who could sign in. It is now one of three (a member's own business, the one
    the operator selected, or the single-business default), and WHO is asking is a question the
    service layer refuses to guess.

    Bundling them is not tidiness. A helper handed a bare ``AdminRuntime`` could still reach the
    service — it would just have to invent a principal — and "invent a principal" is the one move
    that must not be available anywhere. With this, a helper that wants data has to be handed an
    identity first.
    """

    runtime: AdminRuntime
    principal: Principal
    business: str | None


async def _fetch_resources(caller: _Caller) -> list[CalendarResource]:
    """The timeline's rows: the tenant's hosts (RF-28)."""
    rows = await service.list_hosts_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [host_resource(row) for row in rows]


async def _fetch_booking_views(
    caller: _Caller,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[dict[str, str]], list[CalendarEvent]]:
    """Load the tenant's bookings, projected into the FULL-history row list AND the visible-period
    calendar events.

    The two projections have DIFFERENT scopes on purpose (F2-NAV): the row list keeps the FULL
    history (never windowed — its contract), while the calendar events are scoped to the visible
    period via ``date_from`` / ``date_to`` (inclusive calendar dates matched against each booking's
    start) so navigating shows that period's events without preloading everything. Cancelled
    bookings are omitted from the calendar (a reschedule cancels the predecessor, so showing it
    would duplicate the moved chip); the row list keeps them. Before any navigation (no window) a
    single query feeds both; a navigated window adds one scoped query for the events.
    """
    reads: list[BookingRead] = await service.list_bookings_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    rows = [booking_row(read) for read in reads]
    if date_from is None and date_to is None:
        event_reads = reads
    else:
        event_reads = await service.list_bookings_view(
            caller.runtime,
            principal=caller.principal,
            tenant_slug=caller.business,
            date_from=date_from,
            date_to=date_to,
        )
    hosts = await _host_of_event_type(caller)
    events = [
        booking_event(read, resource_id=hosts.get(read.event_type_id))
        for read in event_reads
        if read.status is not BookingStatus.CANCELLED
    ]
    return rows, events


async def _fetch_calendar_events(
    caller: _Caller,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[CalendarEvent]:
    """Load ONLY the visible-period calendar events (one scoped query).

    Navigation uses this instead of :func:`_fetch_booking_views` so moving between periods never
    re-queries the full booking history (perf) — ``state.bookings`` (the full-history rows) is left
    in place, refreshed only on initial load and after mutations that actually change the rows.
    """
    reads = await service.list_bookings_view(
        caller.runtime,
        principal=caller.principal,
        tenant_slug=caller.business,
        date_from=date_from,
        date_to=date_to,
    )
    hosts = await _host_of_event_type(caller)
    return [
        booking_event(read, resource_id=hosts.get(read.event_type_id))
        for read in reads
        if read.status is not BookingStatus.CANCELLED
    ]


def _window(state: AdminState) -> dict[str, date | None]:
    """The (date_from, date_to) DATA-load window for the current calendar view (F2-NAV).

    Both ends are inclusive calendar dates. The PERIOD comes straight from the calendar's own
    on_range_change / on_view_change payload (``calendar_anchor`` = the period's ``from``,
    ``calendar_range_to`` = its EXCLUSIVE upper bound), so there is no duplicated "which period"
    geometry on the Python side — the JS core is the single source of truth. The last INCLUSIVE day
    is the instant just before the exclusive ``to`` (``list_bookings`` treats ``date_to`` as an
    inclusive calendar date). Before any navigation (blank), the window is unbounded (load all).

    For the month VIEW the window is the EXACT 6-week grid extent (``_MONTH_GRID_DAYS`` days from
    the Monday of the 1st's week — the same grid ``getMonthGridDays`` + ``_calendar()`` render).
    That grid shows a variable number of adjacent-month days whose events must be fetched too. This
    is the deliberate "period range vs. data range" split: the emitted period stays re-anchorable
    while the month load covers every day the grid shows. Other views (week / day / agenda-list)
    render only their own period, so their window is exactly that period.
    """
    if not state.calendar_anchor or not state.calendar_range_to:
        return {"date_from": None, "date_to": None}
    try:
        period_start = datetime.fromisoformat(state.calendar_anchor).date()
        period_end = (datetime.fromisoformat(state.calendar_range_to) - timedelta(seconds=1)).date()
    except ValueError:
        return {"date_from": None, "date_to": None}
    if state.calendar_view == "month":
        # The Monday of the week containing the 1st (period_start), then the last of the 42 days.
        # ``weekday()`` is Mon=0, so subtracting it lands on Monday — the admin's Monday-first grid.
        grid_start = period_start - timedelta(days=period_start.weekday())
        grid_end = grid_start + timedelta(days=_MONTH_GRID_DAYS - 1)
        return {"date_from": grid_start, "date_to": grid_end}
    return {"date_from": period_start, "date_to": period_end}


class _ReloadResult(enum.Enum):
    """The outcome of a token-guarded calendar reload (F2-NAV), so a caller can token-guard its own
    follow-up (e.g. only write ``state.error`` when it is still the latest reload)."""

    APPLIED = "applied"  # fetched and applied — this reload is still the latest
    SUPERSEDED = "superseded"  # a newer reload won; nothing was applied, do not touch view/error
    FAILED = "failed"  # the fetch failed AND this reload is still the latest (caller may report it)


#: Free functions (not state methods) on purpose, so Reflex does not turn a plain "reload" helper
#: into a client-callable event handler — the same reason the fetch helpers live here.
#:
#: Out-of-order guard (monotonic ``calendar_reload_seq`` token): rapid prev/next/today — and a
#: mutation's follow-up refetch — issue overlapping async reloads. Each reload takes the NEXT token
#: and captures it before the ``await``; it applies its result ONLY if it is still the latest. A
#: token (not the anchor/range) because two DIFFERENT requests can target the SAME period
#: (A → B → A): a stale A-response must not overwrite the fresh A-response. Same ordering-causal
#: discipline as the F1 outbox / the client reconciliation ``revision``.


async def _reload_calendar(state: AdminState, caller: _Caller) -> None:
    """Navigation reload: refresh ONLY the visible-period calendar events, token-guarded.

    Leaves ``state.bookings`` (the full history) untouched — navigation never re-queries it (perf) —
    and applies the events + clears/sets ``state.error`` only if this reload is still the latest, so
    an out-of-order response from a rapid navigation sequence cannot restore stale events or clobber
    a newer navigation's error.
    """
    state.calendar_reload_seq += 1
    token = state.calendar_reload_seq
    try:
        events = await _fetch_calendar_events(caller, **_window(state))
    except service.AdminError as exc:
        if token == state.calendar_reload_seq:
            state.error = _error_text(exc)
        return
    if token != state.calendar_reload_seq:
        return  # a newer reload was requested while this was in flight — drop the stale result
    state.calendar_events = events
    state.error = ""


async def _commit_calendar_view(state: AdminState, caller: _Caller) -> _ReloadResult:
    """Refresh the FULL rows + the visible-period events after a mutation, token-guarded (F2-NAV).

    Shares the token with the navigation reload, so if a navigation moved the visible period while
    the mutation's DB round-trip was in flight, this (possibly stale-window) refetch is DROPPED
    (returns ``SUPERSEDED``) instead of overwriting the newer period's data. It swallows the fetch
    error and returns ``FAILED`` (when still latest) so the caller writes ``state.error`` only when
    it actually owns the view — a superseded mutation must not publish a stale error over a newer
    navigation.

    The full-history ``bookings`` rows have INDEPENDENT causality (their own token): a navigation
    supersedes the events but never the rows (it doesn't load them), so this mutation's row update
    is applied even when its events refresh is superseded — while a newer load/mutation still wins.
    """
    state.calendar_reload_seq += 1
    state.bookings_reload_seq += 1
    events_token = state.calendar_reload_seq
    rows_token = state.bookings_reload_seq
    try:
        rows, events = await _fetch_booking_views(caller, **_window(state))
    except (service.AdminError, SQLAlchemyError):
        if events_token != state.calendar_reload_seq:
            return _ReloadResult.SUPERSEDED
        return _ReloadResult.FAILED
    if rows_token == state.bookings_reload_seq:
        state.bookings = rows  # rows: dropped only by a newer LOAD/MUTATION, not by navigation
    if events_token != state.calendar_reload_seq:
        return _ReloadResult.SUPERSEDED
    state.calendar_events = events
    return _ReloadResult.APPLIED


def _settle_mutation(state: AdminState, outcome: _ReloadResult) -> None:
    """Write ``state.error`` after a mutation's token-guarded refetch, but ONLY if this reload still
    owns the view: cleared on APPLIED, the resync notice on FAILED, and left UNTOUCHED on SUPERSEDED
    (a newer navigation owns the view + error, so a superseded mutation must not clobber it)."""
    if outcome is _ReloadResult.APPLIED:
        state.error = ""
    elif outcome is _ReloadResult.FAILED:
        state.error = _RESYNC_MESSAGE


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


def _is_cross_host_drag(payload: dict[str, str], snapshot: CalendarEvent) -> bool:
    """Whether this drop landed on a DIFFERENT host's timeline row than the booking belongs to.

    ``resourceId`` is present only on the timeline (RF-28); month / week / day have no resource
    dimension and send none. ==An ABSENT resource is therefore not a DIFFERENT resource==: reading
    the missing key as "moved to nothing" would refuse every ordinary drag in the other four views,
    which is the same class of bug — a guard that fires on the case it was never meant for — as the
    staleness check that marked cancellations delivered without sending them.

    A booking whose event type has vanished has no row either (``resourceId`` absent from the chip);
    it cannot be dragged ACROSS rows because it is on none, so it too falls through to the ordinary
    reschedule.
    """
    target = payload.get("resourceId")
    if target is None:
        return False
    current = snapshot.get("resourceId")
    if current is None:
        return False
    return str(target) != str(current)


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
    state: AdminState,
    caller: _Caller,
    payload: dict[str, str],
    *,
    require_start_change: bool = False,
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
    if _is_cross_host_drag(payload, snapshot):
        # RF-28. Refused BEFORE the optimistic apply, so the chip never even appears to move: an
        # error message under a booking that visibly jumped rows is a lie the operator can see.
        state.error = _CROSS_HOST_DRAG_MESSAGE
        return
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
    try:
        new_start = _parse_admin_start(new_start_raw)
        await service.reschedule_booking_action(
            caller.runtime,
            principal=caller.principal,
            tenant_slug=caller.business,
            booking_id=uuid.UUID(booking_id),
            new_start=new_start,
        )
    except (ValueError, service.AdminError, SQLAlchemyError) as exc:
        # Refused or failed BEFORE commit → roll the chip back to the authoritative slot. Every exit
        # reconciles the optimistic state: no path leaves an unconfirmed move applied.
        state.calendar_events = _with_restored_event(state.calendar_events, snapshot)
        state.error = _error_text(exc)
        return
    # Committed. The reschedule replaced the booking with a new-id successor, so a manage panel open
    # on the old (now cancelled) id is stale — clear it, like reschedule / reschedule_selected do.
    state.selected_booking_id = ""
    # Commit the authoritative state (token-guarded). On a FAILED refresh KEEP the optimistic move
    # (the DB really moved) + resync; on SUPERSEDED a newer navigation owns the view (leave it).
    _settle_mutation(state, await _commit_calendar_view(state, caller))


async def _fetch_event_types(caller: _Caller) -> list[dict[str, str]]:
    rows = await service.list_event_types_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [event_type_row(row) for row in rows]


async def _fetch_hosts(caller: _Caller) -> list[dict[str, str]]:
    rows = await service.list_hosts_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [host_row(row) for row in rows]


async def _fetch_connections(caller: _Caller, host_id: uuid.UUID) -> list[dict[str, str]]:
    rows = await service.list_connections_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business, host_id=host_id
    )
    return [connection_row(row) for row in rows]


def _owner_id(form_data: dict[str, str]) -> uuid.UUID | None:
    """The host who owns a weekly pattern; the sentinel (or blank) → ``None`` = the BUSINESS.

    ``None`` is a real value here, not an absence: a shared pattern is one every host may use. The
    alternative — leaving the field off the form entirely, as it was — is how two hosts come to
    share a schedule without anybody deciding they should (RF-30).
    """
    raw = _clean(form_data, "owner_id")
    if not raw or raw == SHARED_SCHEDULE:
        return None
    return uuid.UUID(raw)


def _owner_update(form_data: dict[str, str]) -> dict[str, uuid.UUID | None]:
    """The owner key for an UPDATE payload, or ``{}`` to leave the stored owner UNTOUCHED.

    ==Three values, and only two of them are a value.== ``schedules.user_id`` is one of the fields
    where ``null`` MEANS something — the pattern belongs to the whole business, and every host may
    use it — so "I did not touch this field" and "give this to nobody" cannot be the same blank.

    Read the blank as ``None`` and renaming a schedule would quietly take it away from its host, and
    two hosts would end up sharing a pattern nobody chose — which is the exact accident the column
    was added to prevent. So an untouched select PRESERVES the owner (the key is omitted, and the
    service leaves it alone via ``model_fields_set``), and handing a schedule back to the business
    is the explicit :data:`SHARED_SCHEDULE` option. Same shape as the rule scope and the EN
    translations: removal is never something a field you did not touch does for you.
    """
    raw = _clean(form_data, "owner_id")
    if not raw:
        return {}
    return {"user_id": None if raw == SHARED_SCHEDULE else uuid.UUID(raw)}


async def _fetch_workflows(caller: _Caller) -> list[dict[str, str]]:
    rows = await service.list_workflows_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [workflow_row(row) for row in rows]


async def _fetch_templates(caller: _Caller) -> list[dict[str, str]]:
    rows = await service.list_templates_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [template_row(row) for row in rows]


# --------------------------------------------------------------------------------------
# Rule-form parsing (RF-24). Both of this form's traps live here.
# --------------------------------------------------------------------------------------

#: The rule form carries ONE kind field per channel, which IS the schema's rule ("one step per
#: channel": two steps on one channel are two dedupe keys, so the guest gets the same message
#: twice). A non-blank kind means "send this on this channel"; blank means no step there.
_STEP_CHANNELS = ("email", "whatsapp", "sms")


def _parse_steps(form_data: dict[str, str]) -> list[WorkflowStepIn]:
    """The steps a rule form describes: one per channel whose ``kind`` field was filled in.

    Positions are assigned CONTIGUOUSLY over the channels actually present, so dropping the middle
    step of three leaves no hole for the (unique-per-workflow) position constraint to trip over.
    """
    steps: list[WorkflowStepIn] = []
    for channel in _STEP_CHANNELS:
        kind = _clean(form_data, f"{channel}_kind")
        if kind:
            steps.append(WorkflowStepIn(channel=channel, kind=kind, position=len(steps)))
    return steps


def _parse_scope(form_data: dict[str, str]) -> uuid.UUID | None:
    """The event type a rule governs on CREATE; the sentinel (or blank) → ``None`` = every one."""
    raw = _clean(form_data, "event_type_id")
    if not raw or raw == ALL_EVENT_TYPES:
        return None
    return uuid.UUID(raw)


def _scope_update(form_data: dict[str, str]) -> dict[str, uuid.UUID | None]:
    """The scope key for an UPDATE payload, or ``{}`` to leave the stored scope UNTOUCHED.

    ``event_type_id`` is the one field of a rule where ``null`` is a real VALUE — "every event type"
    — and not "leave this alone". A blank select therefore cannot be allowed to mean both: an
    untouched select must never silently widen a rule from one event type to ALL of them while the
    operator was editing its offset. That fires the rule across every booking in the business, and
    nothing whatsoever would have said so.

    So widening is EXPLICIT, exactly as clearing an EN translation is: the :data:`ALL_EVENT_TYPES`
    sentinel sets the scope to ``None``, a real id sets that id, and a blank field omits the key.
    """
    raw = _clean(form_data, "event_type_id")
    if not raw:
        return {}
    return {"event_type_id": None if raw == ALL_EVENT_TYPES else uuid.UUID(raw)}


def _steps_update(form_data: dict[str, str]) -> dict[str, list[WorkflowStepIn]]:
    """The steps key for an UPDATE payload, or ``{}`` to leave the stored steps UNTOUCHED.

    ``steps`` REPLACES the list wholesale, and the schema forbids a rule with none — so "every kind
    field blank" cannot mean "remove all the steps" (there is no such rule). It means the operator
    was editing something else, and the steps are left exactly as they were.
    """
    steps = _parse_steps(form_data)
    return {"steps": steps} if steps else {}


async def _fetch_schedules(caller: _Caller) -> list[dict[str, str]]:
    rows = await service.list_schedules_view(
        caller.runtime, principal=caller.principal, tenant_slug=caller.business
    )
    return [schedule_row(row) for row in rows]


def _schedule_id(schedules: list[dict[str, str]], name: str) -> uuid.UUID:
    """Resolve a schedule id by its (tenant-unique) name from the loaded schedule rows."""
    for row in schedules:
        if row["name"] == name:
            return uuid.UUID(row["id"])
    raise ValueError(f"unknown schedule {name!r}")


class AdminState(rx.State):
    """The whole admin session: WHO is signed in (backend-only), WHERE, and the data lists."""

    # Security-critical: backend-only var — never sent to the frontend, no client setter (RF-18).
    _authenticated: bool = False

    # -- WHO (B-02) -----------------------------------------------------------------
    # Backend-only, all three, and written by exactly one kind of place: a login handler that has
    # just verified a password. ==A client cannot set them, and that IS the security property.== The
    # business a panel acts on is derived from these — from a server-side membership check — and
    # never from anything the browser sent.
    #
    # Three plain strings rather than one Principal, because a Reflex backend var has to be a plain
    # serialisable value; :attr:`_principal` reassembles it. An empty ``_principal_kind`` means "no
    # session", and every reader of it FAILS CLOSED.
    _principal_kind: str = ""
    _principal_role: str = ""
    _principal_user_id: str = ""
    # A MEMBER's business, as a uuid, exactly as the server resolved it at login. It is the
    # AUTHORITY
    # every panel compares the requested business against (``service._authorize``), and it is why a
    # member naming another business's slug is refused rather than served. The operator has none:
    # they belong to no business, and theirs is re-resolved from the slug they picked, every time.
    _business_tenant_id: str = ""

    # -- WHERE (B-02) ---------------------------------------------------------------
    # The business being administered. It used to be ``runtime.config.tenant_slug``, full stop —
    # the only possible answer while the instance operator was the only person who could sign in,
    # and one that simply RAISED on an instance with more than one business and no configured slug.
    #
    # It is now one of three, and not one of them is "whatever the browser asked for":
    #   * a MEMBER's — fixed at login from their membership, and never changed after (the
    #     ``select_business`` handler refuses them);
    #   * the OPERATOR's — whichever they picked in the selector (criterion 38), re-resolved
    #     server-side against ``tenants`` on every action;
    #   * blank — the single-business default (``AETHERCAL_ADMIN_TENANT_SLUG``, or the only business
    #     there is). ==That is the mode every live instance runs in today, and it still works.==
    _business_slug: str = ""

    error: str = ""
    bookings: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    event_types: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    schedules: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    # -- notification rules (RF-24) --------------------------------------------------
    workflows: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    templates: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    # -- hosts + their connected calendars (RF-30) -----------------------------------
    # ``hosts`` feeds the host SELECTOR on the event-type form (whose absence was the RF-30 defect)
    # and the schedule-owner select, as well as the hosts table itself.
    hosts: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    # -- the health panel (RF-25 / R9) ------------------------------------------------
    metrics: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    # The connections of the host the operator is currently inspecting (they are per-host, so they
    # are loaded on demand rather than for everybody).
    connections: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    selected_host_id: str = ""

    # -- calendar (F2-F) ------------------------------------------------------------
    # The agenda's calendar events (active bookings) + the operator-selected view. The calendar
    # component reads both; ``calendar_events`` also carries the optimistic in-flight move.
    calendar_events: list[CalendarEvent] = []  # noqa: RUF012 (reflex state var)
    # The timeline's rows (RF-28): the tenant's hosts. Ignored by the other four views.
    calendar_resources: list[CalendarResource] = []  # noqa: RUF012 (reflex state var)
    calendar_view: str = "month"
    # The visible-period anchor (a day within the shown period) + the period's EXCLUSIVE upper
    # bound, both set from the calendar's on_range_change / on_view_change payload.
    # ``calendar_anchor`` feeds the component's ``anchor`` prop; together they scope the booking
    # load to the visible period. Blank (before any navigation) = "today" / unbounded. (F2-NAV)
    calendar_anchor: str = ""
    calendar_range_to: str = ""
    # Monotonic token for the period-load: each reload takes the next value and applies its result
    # only if it is still the latest, so an out-of-order response from a rapid navigation sequence
    # (even back to the SAME period) cannot restore stale events. Server-owned; a client that pokes
    # it can't defeat the guard (each reload increments THEN captures, so the next load re-syncs).
    # (F2-NAV)
    calendar_reload_seq: int = 0
    # A SEPARATE token for the full-history rows: navigation refreshes only the events (never the
    # rows), so a navigation must not drop a mutation's row update. Only loads + mutation refetches
    # bump this, giving `bookings` its own causality independent of the events token. (F2-NAV)
    bookings_reload_seq: int = 0
    # The booking selected by a click, surfaced in the manage panel (view / cancel / reschedule).
    selected_booking_id: str = ""
    selected_booking_start: str = ""
    selected_booking_guest: str = ""
    # The range-select create affordance: the pre-filled start + whether the create panel is open.
    new_booking_start: str = ""
    show_new_booking: bool = False

    # -- members + the business selector (B-02) --------------------------------------
    # ``members`` is what criterion 37 is about: only an OWNER ever sees a row here (the service
    # refuses everybody else — it does not quietly return an empty list). ``businesses`` is the
    # operator's selector (criterion 38); a member's is always empty, because they are refused too.
    members: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)
    businesses: list[dict[str, str]] = []  # noqa: RUF012 (reflex state var)

    # -- who am I (B-02) -------------------------------------------------------------

    @property
    def _principal(self) -> Principal:
        """Reassemble the signed-in principal from the backend vars a login handler wrote.

        ==Fail-closed, and loudly.== An empty ``_principal_kind`` means no login ever ran, and there
        is no sensible principal to invent: inventing one is precisely the hole (a "default" that
        silently means full power). Every handler already returns early on ``_authenticated``, so
        this raise is unreachable through the UI — which is exactly why it must stay a raise. The
        day
        a new handler forgets that guard, this is what stops it, instead of it acting as somebody.

        ==The ``role`` here is the LOGIN SNAPSHOT, and it is NOT what authorises.== It is the role
        the member held when they signed in; an owner may have revoked or demoted them since. The
        service layer re-reads the CURRENT role from ``memberships`` on every action
        (``service._live_principal``) and decides on that, so a stale snapshot role grants nothing —
        the day a member is demoted, their very next action is judged on the new role, not this one.
        This snapshot only seeds who is asking and which business they belong to.
        """
        if self._principal_kind == PrincipalKind.BOOTSTRAP_OPERATOR.value:
            return Principal.bootstrap_operator()
        if self._principal_kind == PrincipalKind.MEMBER.value:
            return Principal.member(
                tenant_id=uuid.UUID(self._business_tenant_id),
                user_id=uuid.UUID(self._principal_user_id),
                role=MemberRole(self._principal_role),
            )
        raise service.AdminPermissionError("you are not signed in")

    @property
    def _business(self) -> str | None:
        """The slug of the business being administered — ``None`` for the single-business default.

        ``None`` is the mode every LIVE instance runs in (``AETHERCAL_ADMIN_TENANT_SLUG`` is
        optional, and ``admin_session(None)`` resolves the only business there is). Not honouring it
        would leave the first live admin with no door at all.
        """
        return self._business_slug or self.__runtime_slug()

    @property
    def _caller(self) -> _Caller:
        """WHO is asking + WHERE, bundled so no fetch helper can invent either of them.

        ==A property, not a public method, on purpose.== Reflex wraps every PUBLIC method of an
        ``rx.State`` into a client-callable event handler; a bundle of the operator's identity is
        not something the websocket should be able to invoke. A property is never wrapped, and the
        module-level calendar helpers that need it are handed it as a plain argument
        (``_reload_calendar(state, caller)`` and friends) rather than reaching across into
        ``state._caller`` — so the encapsulation holds AND nothing new is exposed.
        """
        return _Caller(
            runtime=current_runtime(), principal=self._principal, business=self._business
        )

    def __runtime_slug(self) -> str | None:
        try:
            return current_runtime().config.tenant_slug
        except RuntimeError:  # pragma: no cover - the admin is always built before it is used
            return None

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
            # ==The instance's OPERATOR — not a member of anything.== Their business is the one they
            # select (or the single default), and it is re-resolved server-side on every action.
            self._principal_kind = PrincipalKind.BOOTSTRAP_OPERATOR.value
            self._principal_role = ""
            self._principal_user_id = ""
            self._business_tenant_id = ""
            self._business_slug = ""
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
    async def member_login(self, form_data: dict[str, str]) -> EventSpec | None:
        """Sign a MEMBER of one business in — ==criterion 39, and the login is PER BUSINESS.==

        The business comes from the route (``/admin/{tenant_slug}/login``), never from the address
        alone, and the reason is a fact about the schema: ``users`` is unique on ``(tenant_id,
        lower(email))``, so ``ana@example.com`` can be **two different people in two businesses**,
        with two passwords and two roles. An address-only login would have to guess which of them
        was
        signing in — and every way of guessing is a defect (take the first row and you authenticate
        somebody as a person they are not; search every business and the form becomes an oracle for
        who exists where). With the slug in the route there is nothing left to guess.

        The slug confers NO authority: it only selects whose password is about to be checked. What
        authorises is the password, and what the session then carries is the business the SERVER
        verified — never the one the browser named.

        Rate-limited and off-loop exactly like the operator's login (the PBKDF2 is the same
        deliberately slow derivation), and its refusals are the same single sentence: an unknown
        business, an unknown address, a wrong password and a host with no membership are ONE
        message.
        """
        tenant_slug = _clean(form_data, "tenant_slug")
        email = _clean(form_data, "email")
        # The rate-limit budget is per (ip, business, address): a login flood against Acme must not
        # lock Globex's people out of their own panel.
        keys = _rate_keys(self, f"{tenant_slug}:{email}")
        if LOGIN_LIMITER.any_locked(keys):
            self.error = _LOCKED_OUT_MESSAGE
            return None
        password = str(form_data.get("password", ""))
        try:
            async with PBKDF2_LIMITER.slot():
                if LOGIN_LIMITER.any_locked(keys):
                    self.error = _LOCKED_OUT_MESSAGE
                    return None
                principal = await bootstrap.member_login(
                    current_runtime(),
                    tenant_slug=tenant_slug,
                    email=email,
                    password=password,
                )
        except LoginThrottledError:
            self.error = _LOCKED_OUT_MESSAGE
            return None

        if principal is None or principal.tenant_id is None or principal.user_id is None:
            self._authenticated = False
            for key in keys:
                LOGIN_LIMITER.record_failure(key)
            self.error = (
                _LOCKED_OUT_MESSAGE
                if LOGIN_LIMITER.any_locked(keys)
                else "Invalid email or password."
            )
            return None

        self._authenticated = True
        self._principal_kind = PrincipalKind.MEMBER.value
        self._principal_role = (principal.role or MemberRole.MEMBER).value
        self._principal_user_id = str(principal.user_id)
        # ==The authority, written by the server.== Every panel compares the business it is asked
        # for
        # against this; a member who names another business's slug is refused, not served.
        self._business_tenant_id = str(principal.tenant_id)
        self._business_slug = tenant_slug
        for key in keys:
            LOGIN_LIMITER.record_success(key)
        self.error = ""
        return rx.redirect(HOME_ROUTE)

    @rx.event
    def logout(self) -> EventSpec:
        """Clear the session and return to the login page. ==Every field of it, not just the flag.==

        A ``_principal_kind`` left behind by the last person to use this browser tab is a session
        that outlives its logout: the next handler would rebuild THEIR principal. The flag is what
        the guards read, but the identity is what the service layer acts on.
        """
        self._authenticated = False
        self._principal_kind = ""
        self._principal_role = ""
        self._principal_user_id = ""
        self._business_tenant_id = ""
        self._business_slug = ""
        self.members = []
        self.businesses = []
        return rx.redirect(LOGIN_ROUTE)

    @rx.event
    def require_auth(self) -> EventSpec | None:
        """``on_load`` guard for every protected page: bounce to login when not authenticated."""
        if not self._authenticated:
            return rx.redirect(LOGIN_ROUTE)
        return None

    # -- the business selector (B-02, criterion 38) ----------------------------------

    @rx.event
    async def load_businesses(self) -> None:
        """The businesses the OPERATOR may switch between. A member's list stays empty — refused.

        Before B-02, an instance with two businesses and no ``AETHERCAL_ADMIN_TENANT_SLUG`` simply
        raised at the admin's boot. This is what replaces that: the operator picks.
        """
        if not self._authenticated:
            return
        try:
            rows = await bootstrap.list_businesses(current_runtime(), principal=self._principal)
        except (service.AdminError, rbac.PermissionDeniedError):
            # A member asking for the selector is refused, and correctly sees nothing to switch to.
            self.businesses = []
            return
        self.businesses = [{"slug": row.slug, "name": row.name} for row in rows]

    @rx.event
    async def select_business(self, slug: str) -> None:
        """Switch the OPERATOR to another business — ==criterion 38, and only they may do it.==

        ``require_operator`` is not a capability and cannot be: ``owner`` means "everything **in my
        business**", so an owner able to step into another one would be the cross-business
        escalation
        the whole batch exists to prevent. A member calling this handler directly over the websocket
        (which a client can always do) is refused HERE, and would be refused again at every panel by
        ``service._authorize`` — the slug they set would not match the business their membership was
        verified against.
        """
        if not self._authenticated:
            return
        try:
            rbac.require_operator(self._principal)
        except rbac.PermissionDeniedError as exc:
            self.error = str(exc)
            return
        self._business_slug = slug.strip()
        self.error = ""
        await self.load_bookings()

    # -- members (B-02, criterion 37) -------------------------------------------------

    @rx.event
    async def load_members(self) -> None:
        """Who is in this business. ==Owner only== — a member is REFUSED, not served an empty
        list."""
        if not self._authenticated:
            return
        try:
            rows = await service.list_members_view(
                current_runtime(), principal=self._principal, tenant_slug=self._business
            )
            self.hosts = await _fetch_hosts(self._caller)
        except service.AdminError as exc:
            self.members = []
            self.error = _error_text(exc)
            return
        self.members = [member_row(row) for row in rows]
        self.error = ""

    @rx.event
    async def create_member(self, form_data: dict[str, str]) -> None:
        """Let one of the business's hosts into the panel, with a role and an initial password.

        ==Invitation by email is F5, declared== — no mail round-trip, no reset link. The owner hands
        the initial password over out of band, and the member changes it.
        """
        if not self._authenticated:
            return
        try:
            form = service.MemberForm(
                host_id=uuid.UUID(_clean(form_data, "host_id")),
                role=MemberRole(_clean(form_data, "role")),
                password=str(form_data.get("password", "")) or None,
            )
        except ValueError:
            self.error = "Choose a host and a role."
            return
        try:
            await service.create_member_action(
                current_runtime(),
                principal=self._principal,
                tenant_slug=self._business,
                form=form,
            )
        except service.AdminError as exc:
            self.error = _error_text(exc)
            return
        await self.load_members()

    @rx.event
    async def update_member_role(self, form_data: dict[str, str]) -> None:
        """Change what a member may do. The service refuses to demote the LAST owner."""
        if not self._authenticated:
            return
        try:
            membership_id = uuid.UUID(_clean(form_data, "membership_id"))
            role = MemberRole(_clean(form_data, "role"))
        except ValueError:
            self.error = "Choose a role."
            return
        try:
            await service.update_member_role_action(
                current_runtime(),
                principal=self._principal,
                tenant_slug=self._business,
                membership_id=membership_id,
                role=role,
            )
        except service.AdminError as exc:
            self.error = _error_text(exc)
            return
        await self.load_members()

    @rx.event
    async def set_member_password(self, form_data: dict[str, str]) -> None:
        """An owner sets a member's password — the answer to "I lost mine" while recovery is F5."""
        if not self._authenticated:
            return
        try:
            membership_id = uuid.UUID(_clean(form_data, "membership_id"))
        except ValueError:
            self.error = "Choose a member."
            return
        try:
            await service.set_member_password_action(
                current_runtime(),
                principal=self._principal,
                tenant_slug=self._business,
                membership_id=membership_id,
                password=str(form_data.get("password", "")),
            )
        except service.AdminError as exc:
            self.error = _error_text(exc)
            return
        await self.load_members()

    @rx.event
    async def delete_member(self, membership_id: str) -> None:
        """Remove somebody from the panel. They stay a HOST; the service refuses the last owner."""
        if not self._authenticated:
            return
        try:
            await service.delete_member_action(
                current_runtime(),
                principal=self._principal,
                tenant_slug=self._business,
                membership_id=uuid.UUID(membership_id),
            )
        except (ValueError, service.AdminError) as exc:
            self.error = (
                _error_text(exc) if isinstance(exc, service.AdminError) else "Unknown member."
            )
            return
        await self.load_members()

    # -- bookings -------------------------------------------------------------------

    @rx.event
    async def load_bookings(self) -> None:
        """Load the tenant's full-history rows + the visible-period calendar events (``on_load``).

        Token-guarded like navigation, so an on-load refresh can't clobber a period the operator has
        already moved to, nor an in-flight navigation clobber this initial load.
        """
        if not self._authenticated:
            return
        self.calendar_reload_seq += 1
        self.bookings_reload_seq += 1
        events_token = self.calendar_reload_seq
        rows_token = self.bookings_reload_seq
        try:
            rows, events = await _fetch_booking_views(self._caller, **_window(self))
            # The timeline's rows (RF-28). They change only when a host is added or removed, so they
            # are loaded here rather than on every navigation.
            resources = await _fetch_resources(self._caller)
        except service.AdminError as exc:
            if events_token == self.calendar_reload_seq:
                self.error = _error_text(exc)
            return
        if rows_token == self.bookings_reload_seq:
            self.bookings = rows
        if events_token != self.calendar_reload_seq:
            return
        self.calendar_events = events
        self.calendar_resources = resources
        self.error = ""

    @rx.event
    async def cancel(self, booking_id: str) -> None:
        """Cancel the booking with ``booking_id`` and refresh the agenda."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.cancel_booking_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                booking_id=uuid.UUID(booking_id),
            )
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)
            return
        self.selected_booking_id = ""
        _settle_mutation(self, await _commit_calendar_view(self, self._caller))

    @rx.event
    async def mark_no_show(self, booking_id: str) -> None:
        """Mark the selected booking a no-show (RF-25) and refresh the agenda.

        The button is always offered rather than hidden by a rule computed here: whether a booking
        MAY be marked depends on its status and on whether it has ended, and the only authority on
        both is the database at the moment of the click — a chip on the client can be minutes stale.
        A refusal now arrives in the service's own words ("only a confirmed booking can be marked a
        no-show", "a booking can only be marked a no-show after it has ended"), so the loud refusal
        tells the operator more than a silently disabled button ever would.

        The booking KEEPS ITS SLOT, so unlike a cancellation it stays on the calendar — as a muted,
        non-draggable chip, because ``booking_event`` makes only a CONFIRMED booking editable.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.mark_no_show_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                booking_id=uuid.UUID(booking_id),
            )
        except (ValueError, service.AdminError, SQLAlchemyError) as exc:
            self.error = _error_text(exc)
            return
        self.selected_booking_id = ""
        _settle_mutation(self, await _commit_calendar_view(self, self._caller))

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
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                booking_id=booking_id,
                new_start=new_start,
            )
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)
            return
        self.selected_booking_id = ""
        _settle_mutation(self, await _commit_calendar_view(self, self._caller))

    # -- calendar interactions (F2-F) -----------------------------------------------

    @rx.event
    def set_calendar_view(self, view: str) -> None:
        """Switch the calendar surface (month / week / day / list) from the view toggle."""
        if not self._authenticated:
            return
        if view in _VALID_CALENDAR_VIEWS:
            self.calendar_view = view

    @rx.event
    async def on_calendar_range_change(self, payload: dict[str, str]) -> None:
        """Navigate to a new visible period (previous / today / next) and load its events (F2-NAV).

        The calendar's built-in toolbar emits ``{view, from, to}``; ``from`` becomes the new anchor
        and ``[from, to)`` scopes the booking load — so the agenda shows the events of the period
        the operator is looking at, not only the ones around today.
        """
        if not self._authenticated:
            return
        self.calendar_anchor = str(payload.get("from", ""))
        self.calendar_range_to = str(payload.get("to", ""))
        await _reload_calendar(self, self._caller)

    @rx.event
    async def on_calendar_view_change(self, payload: dict[str, str]) -> None:
        """Switch the view from the toolbar's switcher and load the new period's events (F2-NAV).

        An unrecognized view is ignored (never navigate on a bad value) — the view stays put and no
        load happens, mirroring ``set_calendar_view``'s validation.
        """
        if not self._authenticated:
            return
        view = str(payload.get("view", ""))
        if view not in _VALID_CALENDAR_VIEWS:
            return
        self.calendar_view = view
        self.calendar_anchor = str(payload.get("from", ""))
        self.calendar_range_to = str(payload.get("to", ""))
        await _reload_calendar(self, self._caller)

    @rx.event
    async def on_calendar_event_drop(self, payload: dict[str, str]) -> AsyncIterator[None]:
        """Drag an event onto a new day/time → reschedule, optimistically (RF-21)."""
        if not self._authenticated:
            return
        async for _ in _optimistic_reschedule(self, self._caller, payload):
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
        async for _ in _optimistic_reschedule(
            self, self._caller, payload, require_start_change=True
        ):
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
        try:
            new_start = _parse_admin_start(_clean(form_data, "new_start"))
            await service.reschedule_booking_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                booking_id=uuid.UUID(self.selected_booking_id),
                new_start=new_start,
            )
        except (ValueError, service.AdminError, SQLAlchemyError) as exc:
            self.error = _error_text(exc)
            return
        # Committed: clear the now-stale panel (it pointed at the replaced booking), then refresh
        # the view token-guarded — a FAILED refresh asks for a reload, SUPERSEDED defers to the nav.
        self.selected_booking_id = ""
        _settle_mutation(self, await _commit_calendar_view(self, self._caller))

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
        try:
            form = service.BookingForm(
                event_type_id=uuid.UUID(_clean(form_data, "event_type_id")),
                start=_parse_admin_start(_clean(form_data, "start")),
                guest_name=_clean(form_data, "guest_name"),
                guest_email=_clean(form_data, "guest_email"),
                guest_timezone=_clean(form_data, "guest_timezone") or "UTC",
            )
            await service.create_booking_action(
                runtime, principal=self._principal, tenant_slug=self._business, form=form
            )
        except (ValueError, service.AdminError, SQLAlchemyError) as exc:
            self.error = _error_text(exc)
            return
        # Committed: close the form (so the operator can't re-submit an ambiguous retry), then
        # refresh the view token-guarded — FAILED asks for a reload, SUPERSEDED defers to the nav.
        self.show_new_booking = False
        self.new_booking_start = ""
        _settle_mutation(self, await _commit_calendar_view(self, self._caller))

    # -- event types ----------------------------------------------------------------

    @rx.event
    async def load_event_types(self) -> None:
        """Load event types + schedules + HOSTS.

        The hosts are what the RF-30 host selector is built from. Without them the form has no host
        field, and the service used to fill the gap by taking the tenant's first user — which is the
        whole reason a second host could never be given an event type.
        """
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.event_types = await _fetch_event_types(self._caller)
            self.schedules = await _fetch_schedules(self._caller)
            self.hosts = await _fetch_hosts(self._caller)
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
                # RF-30: the host the operator PICKED. This used to be injected from the context —
                # the tenant's first user — so a second host could never be given an event type.
                host_id=uuid.UUID(_clean(form_data, "host_id")),
                slug=_clean(form_data, "slug"),
                title=_clean(form_data, "title"),
                schedule_id=_schedule_id(self.schedules, _clean(form_data, "schedule")),
                duration_seconds=int(_clean(form_data, "duration_min")) * 60,
                max_advance_seconds=int(_clean(form_data, "max_advance_days")) * 86_400,
                title_translations=_en_translation(form_data, "title_en"),
                description_translations=_en_translation(form_data, "description_en"),
            )
            await service.create_event_type_action(
                runtime, principal=self._principal, tenant_slug=self._business, form=form
            )
            self.event_types = await _fetch_event_types(self._caller)
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
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                event_type_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.event_types = await _fetch_event_types(self._caller)
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
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                event_type_id=uuid.UUID(event_type_id),
            )
            self.event_types = await _fetch_event_types(self._caller)
            # Do not report success for a no-op: an unknown id must surface as an error, not
            # silently "succeed" (the service returns False rather than raising for an absent row).
            self.error = "" if existed else "Event type not found"
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    # -- schedules ------------------------------------------------------------------

    @rx.event
    async def load_schedules(self) -> None:
        """Load the tenant's schedules + hosts (the owner select needs the hosts — RF-30)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.schedules = await _fetch_schedules(self._caller)
            self.hosts = await _fetch_hosts(self._caller)
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
                # RF-30: whose pattern this is. Blank / the sentinel = the BUSINESS's, usable by
                # every host. The field's absence is how two hosts came to share a schedule without
                # anybody deciding they should.
                user_id=_owner_id(form_data),
                rules=weekly_rules(
                    parse_weekdays(_clean(form_data, "weekdays")),
                    _clean(form_data, "start"),
                    _clean(form_data, "end"),
                ),
            )
            await service.create_schedule_action(
                runtime, principal=self._principal, tenant_slug=self._business, data=data
            )
            self.schedules = await _fetch_schedules(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_schedule(self, form_data: dict[str, str]) -> None:
        """Update a schedule's name / timezone / OWNER (blank fields left unchanged).

        The owner (RF-30) is what this form was missing. ``schedules.user_id`` shipped with the
        column and no way to move it, so a schedule created for one host could never be transferred
        and a shared one could never be assigned — a field the database has and the panel cannot
        reach is a field that does not exist.

        It is three-valued, and :func:`_owner_update` is what keeps the three values apart.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            fields: dict[str, object] = {}
            name = _clean(form_data, "name")
            if name:
                fields["name"] = name
            timezone = _clean(form_data, "timezone")
            if timezone:
                fields["timezone"] = timezone
            fields.update(_owner_update(form_data))
            # ``model_validate``, not the constructor: the service reads the owner through
            # ``model_fields_set``, and a key passed explicitly as ``None`` would arrive as "hand
            # this schedule back to the whole business" rather than "leave the owner alone".
            data = ScheduleUpdate.model_validate(fields)
            await service.update_schedule_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                schedule_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.schedules = await _fetch_schedules(self._caller)
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
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                schedule_id=uuid.UUID(schedule_id),
            )
            self.schedules = await _fetch_schedules(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    # -- the health panel (RF-25 / R9) ------------------------------------------------

    @rx.event
    async def load_metrics(self) -> None:
        """Read this BUSINESS's outbox backlog and no-show rate (``on_load`` of the health page).

        Tenant-scoped, deliberately: ``observability.collect_metrics`` is instance-wide because it
        feeds the operator's ``/metrics``, and rendering that here would show this business the
        pipeline volume of every other business on the instance.
        """
        if not self._authenticated:
            return
        self.error = ""
        try:
            snapshot = await service.metrics_view(
                current_runtime(), principal=self._principal, tenant_slug=self._business
            )
            self.metrics = metrics_rows(snapshot)
        except service.AdminError as exc:
            self.error = _error_text(exc)

    # -- hosts + their connected calendars (RF-30) -----------------------------------

    @rx.event
    async def load_hosts(self) -> None:
        """Load the tenant's hosts (``on_load`` of the hosts page)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.hosts = await _fetch_hosts(self._caller)
        except service.AdminError as exc:
            self.error = _error_text(exc)

    @rx.event
    async def create_host(self, form_data: dict[str, str]) -> None:
        """Add a host to the business."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.create_host_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                form=service.HostForm(
                    name=_clean(form_data, "name"),
                    email=_clean(form_data, "email"),
                    timezone=_clean(form_data, "timezone") or "UTC",
                ),
            )
            self.hosts = await _fetch_hosts(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_host(self, form_data: dict[str, str]) -> None:
        """Edit a host's name / email / timezone (all three are sent, not patched)."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.update_host_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                host_id=uuid.UUID(_clean(form_data, "id")),
                form=service.HostForm(
                    name=_clean(form_data, "name"),
                    email=_clean(form_data, "email"),
                    timezone=_clean(form_data, "timezone") or "UTC",
                ),
            )
            self.hosts = await _fetch_hosts(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def delete_host(self, host_id: str) -> None:
        """Remove a host — refused while an event type or a schedule of theirs still exists."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.delete_host_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                host_id=uuid.UUID(host_id),
            )
            self.hosts = await _fetch_hosts(self._caller)
            if self.selected_host_id == host_id:
                self.selected_host_id = ""
                self.connections = []
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def select_host(self, host_id: str) -> None:
        """Show a host's connected calendar accounts (so their write target can be designated)."""
        if not self._authenticated:
            return
        try:
            self.connections = await _fetch_connections(self._caller, uuid.UUID(host_id))
            self.selected_host_id = host_id
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def designate_calendar(self, form_data: dict[str, str]) -> None:
        """Send this connection's bookings to a NAMED calendar (the admin's ``--calendar-id``).

        Until now ``"primary"`` was a hard-coded constant, so a host's bookings could only land in
        the connected account's own primary calendar — a personal one, for anybody who connects a
        real account.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.designate_calendar_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                connection_id=uuid.UUID(_clean(form_data, "connection_id")),
                calendar_id=_clean(form_data, "calendar_id"),
            )
            if self.selected_host_id:
                self.connections = await _fetch_connections(
                    self._caller, uuid.UUID(self.selected_host_id)
                )
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    # -- notification rules + templates (RF-24) --------------------------------------

    @rx.event
    async def load_workflows(self) -> None:
        """Load the rules, the templates, and the event types the scope select offers (``on_load``).

        All three, because the rule form cannot be filled in without the other two: a step needs a
        template to render its body, and a rule needs the event types to choose its scope from.
        """
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.workflows = await _fetch_workflows(self._caller)
            self.templates = await _fetch_templates(self._caller)
            self.event_types = await _fetch_event_types(self._caller)
        except service.AdminError as exc:
            self.error = _error_text(exc)

    @rx.event
    async def create_workflow(self, form_data: dict[str, str]) -> None:
        """Author a rule. It is ARMED against the bookings already on the books, not just future
        ones — the service reconciles their queued steps, which is the whole point of the screen."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            data = WorkflowCreate(
                name=_clean(form_data, "name"),
                trigger=_clean(form_data, "trigger"),  # type: ignore[arg-type]  # schema validates
                offset_minutes=int(_clean(form_data, "offset_min") or 0),
                event_type_id=_parse_scope(form_data),
                steps=_parse_steps(form_data),
            )
            await service.create_workflow_action(
                runtime, principal=self._principal, tenant_slug=self._business, data=data
            )
            self.workflows = await _fetch_workflows(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_workflow(self, form_data: dict[str, str]) -> None:
        """Edit a rule, and MAKE THE EDIT TRUE for every booking it already governs.

        Only the fields the operator actually filled in are sent (``exclude_unset``): a blank field
        leaves the stored value alone. The two fields where "blank" would otherwise be ambiguous —
        the scope (``null`` is a real value there) and the steps (which replace the list wholesale)
        — are decided by :func:`_scope_update` / :func:`_steps_update`, never by a default.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            fields: dict[str, object] = {}
            name = _clean(form_data, "name")
            if name:
                fields["name"] = name
            trigger = _clean(form_data, "trigger")
            if trigger:
                fields["trigger"] = trigger
            offset = _clean(form_data, "offset_min")
            if offset:
                fields["offset_minutes"] = int(offset)
            fields.update(_scope_update(form_data))
            fields.update(_steps_update(form_data))
            data = WorkflowUpdate.model_validate(fields)
            await service.update_workflow_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                workflow_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.workflows = await _fetch_workflows(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def activate_workflow(self, workflow_id: str) -> None:
        """Switch a rule ON: it re-arms the bookings taken while it was off, and re-times the steps
        that came due meanwhile (they are made DUE, never destroyed)."""
        if not self._authenticated:
            return
        await self._set_workflow_active(workflow_id, active=True)

    @rx.event
    async def deactivate_workflow(self, workflow_id: str) -> None:
        """Switch a rule OFF: its queued messages are PAUSED, not destroyed — the toggle is
        symmetric, and switching it back on delivers them."""
        if not self._authenticated:
            return
        await self._set_workflow_active(workflow_id, active=False)

    async def _set_workflow_active(self, workflow_id: str, *, active: bool) -> None:
        """The shared body of the two toggles. NOT an ``@rx.event`` — a private helper Reflex must
        not expose as a client-callable handler (the same reason the fetch helpers are free
        functions). The public handlers own the auth guard."""
        runtime = current_runtime()
        try:
            await service.set_workflow_active_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                workflow_id=uuid.UUID(workflow_id),
                active=active,
            )
            self.workflows = await _fetch_workflows(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def create_template(self, form_data: dict[str, str]) -> None:
        """Store the body one ``(channel, kind, locale)`` renders. The body is DATA: the schema's
        allow-list decides which ``{{variables}}`` may appear, and nothing is ever evaluated."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            data = WorkflowTemplateCreate(
                channel=_clean(form_data, "channel"),  # type: ignore[arg-type]  # schema validates
                kind=_clean(form_data, "kind"),
                locale=_clean(form_data, "locale"),
                # An email needs a subject and the phone channels forbid one; the schema refuses the
                # incoherent pair, so a blank is passed on as the ``None`` it means, not as "".
                subject=_clean(form_data, "subject") or None,
                body=_clean(form_data, "body"),
            )
            await service.create_template_action(
                runtime, principal=self._principal, tenant_slug=self._business, data=data
            )
            self.templates = await _fetch_templates(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def update_template(self, form_data: dict[str, str]) -> None:
        """Edit a template's TEXT. Its ``(channel, kind, locale)`` identity is immutable by design:
        re-pointing it would silently change what every step resolving through it sends.

        A blank field leaves the stored text alone. ``subject`` is NOT nullable from here — the
        service refuses to null an email's subject line anyway (an email that arrives blank), so a
        blank field means "unchanged", and removing a subject means deleting the template.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            fields: dict[str, str] = {}
            subject = _clean(form_data, "subject")
            if subject:
                fields["subject"] = subject
            body = _clean(form_data, "body")
            if body:
                fields["body"] = body
            data = WorkflowTemplateUpdate.model_validate(fields)
            await service.update_template_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                template_id=uuid.UUID(_clean(form_data, "id")),
                data=data,
            )
            self.templates = await _fetch_templates(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def delete_template(self, template_id: str) -> None:
        """Delete a template — refused while it is the last body a live step can render (deleting it
        would leave that step reading ``active: true`` and silently messaging nobody)."""
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            await service.delete_template_action(
                runtime,
                principal=self._principal,
                tenant_slug=self._business,
                template_id=uuid.UUID(template_id),
            )
            self.templates = await _fetch_templates(self._caller)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)


__all__ = ["HOME_ROUTE", "LOGIN_ROUTE", "AdminState"]
