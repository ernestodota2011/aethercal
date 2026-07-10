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
from datetime import UTC, datetime

import reflex as rx
from reflex.event import EventSpec

from aethercal.schemas.event_types import EventTypeUpdate
from aethercal.schemas.schedules import ScheduleCreate, ScheduleUpdate
from aethercal.server.admin import service
from aethercal.server.admin.auth import authenticate
from aethercal.server.admin.format import (
    booking_row,
    event_type_row,
    parse_weekdays,
    schedule_row,
    weekly_rules,
)
from aethercal.server.admin.ratelimit import LOGIN_LIMITER, PBKDF2_LIMITER, LoginThrottledError
from aethercal.server.admin.runtime import AdminRuntime, current_runtime

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


async def _fetch_bookings(runtime: AdminRuntime) -> list[dict[str, str]]:
    rows = await service.list_bookings_view(
        runtime.sessionmaker, tenant_slug=runtime.config.tenant_slug
    )
    return [booking_row(row) for row in rows]


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
        """Load the tenant's bookings into the agenda (``on_load``)."""
        if not self._authenticated:
            return
        self.error = ""
        try:
            self.bookings = await _fetch_bookings(current_runtime())
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
            self.bookings = await _fetch_bookings(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
            self.error = _error_text(exc)

    @rx.event
    async def reschedule(self, form_data: dict[str, str]) -> None:
        """Reschedule a booking to a new start.

        The ``datetime-local`` field is timezone-naive; the admin's contract is that its times are
        UTC, so a naive value is stamped as UTC explicitly here (rather than relying on a downstream
        default) — an aware value is honored as sent.
        """
        if not self._authenticated:
            return
        runtime = current_runtime()
        try:
            booking_id = uuid.UUID(_clean(form_data, "booking_id"))
            new_start = datetime.fromisoformat(_clean(form_data, "new_start"))
            if new_start.tzinfo is None:
                new_start = new_start.replace(tzinfo=UTC)
            await service.reschedule_booking_action(
                runtime.sessionmaker,
                tenant_slug=runtime.config.tenant_slug,
                booking_id=booking_id,
                new_start=new_start,
            )
            self.bookings = await _fetch_bookings(runtime)
            self.error = ""
        except (ValueError, service.AdminError) as exc:
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
