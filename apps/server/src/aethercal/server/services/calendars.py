"""Google-calendar service layer: connection storage, busy cache, and the booking event lifecycle.

This is the seam the slots engine (F1-04) and the booking service (F1-05) reach Google through,
implementing RF-11 (connect a Google account), RF-12 (a TTL busy cache so slot math never calls
Google in the request path) and RF-13 (safe degradation -- never treat an unknown/unreachable
calendar as free).

Design notes:

* **Credentials are encrypted at rest.** The OAuth token JSON lives in
  ``external_connections.encrypted_credentials`` as Fernet ciphertext (key derived from the single
  app secret; see ``crypto.derive_fernet_key``). The plaintext token never touches the source or
  the logs. The ``Fernet`` is injected by the caller (the API/CLI derives it from ``Settings``), so
  no secret is read here.
* **The live Google client is INJECTED.** Every testable function takes an already-built ``service``
  (or a ``service_factory``) rather than building the untyped google-api-python-client itself, so
  the whole module is driven offline by a fake. The untyped SDK stays behind the ``Any`` seam in
  ``integrations/google`` exactly as the F0-11 spike established; ``build_live_service`` is the only
  production wiring and is ``# pragma: no cover - live``.
* **Parameter objects.** Related inputs are bundled into small frozen value objects
  (``GoogleCredential``, ``BusyQuery``, and the existing ``MeetEventRequest``) so signatures stay
  small and self-documenting -- the house convention for this codebase.

RF-12/13 freshness + degradation contract (``read_busy``): freshness is WINDOW-AWARE. The cache is
usable for a query only if it is both time-fresh AND its synced coverage window fully contains the
queried window -- a cache filled for one window never answers a query about another (that is how a
double-booking slips through). The result is one of FRESH / STALE / UNAVAILABLE. ``FRESH`` = data
from a covered + time-fresh cache or a successful refresh (or "no connection" -> empty; an
empty-but-covered window is FRESH with no busy). ``STALE`` = a refresh failed and the prior coverage
fully contained the window, so we serve the last-known (complete-for-this-window) copy
(``is_degraded``); slots may still be offered. ``UNAVAILABLE`` = a refresh failed with partial or
absent coverage and we cannot reach Google (``not is_available``) -- the slots engine MUST refuse to
offer this host's slots rather than serve incomplete data as complete and risk a double-booking.

CalendarSyncError contract (event lifecycle): a Google mutation that fails raises
``CalendarSyncError``. Booking success must NOT hard-depend on Google being reachable, so the caller
(F1-05) catches it, confirms the booking anyway, flags it for retry, and logs -- it does not roll
the booking back.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import TimeInterval
from aethercal.server.db.models import BusyCache, ExternalConnection
from aethercal.server.integrations.google.calendar import (
    build_service,
    delete_event,
    insert_event_with_meet,
    query_busy,
)
from aethercal.server.integrations.google.oauth import credentials_from_token_json
from aethercal.server.integrations.google.parse import MeetEventRequest

_logger = logging.getLogger(__name__)

GOOGLE_PROVIDER = "google"
# F1-07 reads/writes the connected account's primary calendar; multi-calendar support (via the
# ExternalCalendarLink rows) is a later wave, so the calendar id is an internal constant.
_DEFAULT_CALENDAR_ID = "primary"

# Builds a live Google ``service`` for a connection. Injected into ``read_busy`` so the refresh path
# is fully faked offline; the production factory is ``build_live_service`` (bound via partial).
ServiceFactory = Callable[[ExternalConnection], Any]


class CalendarSyncError(RuntimeError):
    """A Google Calendar mutation (create/delete/reschedule an event) failed.

    The booking caller (F1-05) catches this to confirm the booking anyway, flag the sync for retry,
    and log -- booking success must never hard-depend on Google being reachable (RF-13 for writes).
    """


@dataclass(frozen=True)
class GoogleCredential:
    """The OAuth consent result: which Google account, and its serialized token JSON (RF-11)."""

    account_email: str
    token_json: str


@dataclass(frozen=True)
class BusyQuery:
    """A busy-cache read request: the horizon, the clock, and the freshness TTL (RF-12/13)."""

    window: TimeInterval
    now: datetime
    ttl: timedelta


class BusyStatus(Enum):
    """The provenance of a ``read_busy`` result (RF-13)."""

    FRESH = "fresh"  # fresh cache, a successful refresh, or no connected calendar (empty busy).
    STALE = "stale"  # refresh failed; serving the last-known copy (degraded, still offerable).
    UNAVAILABLE = "unavailable"  # connection exists but is unreadable -- slots must be refused.


@dataclass(frozen=True)
class BusyReadResult:
    """The busy set for a host over a window, plus how trustworthy it is (RF-13)."""

    status: BusyStatus
    busy: tuple[TimeInterval, ...]

    @property
    def is_available(self) -> bool:
        """False only when UNAVAILABLE -- the slots engine must then refuse this host's slots."""
        return self.status is not BusyStatus.UNAVAILABLE

    @property
    def is_degraded(self) -> bool:
        """True when the busy set is a last-known copy served because a refresh failed."""
        return self.status is BusyStatus.STALE


# --------------------------------------------------------------------------------------
# 1. Connection storage -- OAuth token JSON encrypted at rest (RF-11).
# --------------------------------------------------------------------------------------


async def store_google_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    credential: GoogleCredential,
    fernet: Fernet,
) -> ExternalConnection:
    """Persist (or refresh) a host's Google connection with the token JSON encrypted at rest.

    ``credential.token_json`` is the authorized-user JSON from ``oauth.get_credentials``
    (``creds.to_json()``). It is Fernet-encrypted before it ever hits the row; the plaintext is
    never stored or logged. If a connection already exists for
    ``(tenant, user, provider, account_email)`` its ciphertext is updated in place. The row is
    flushed (ids/defaults populated) but not committed -- the caller owns the transaction.
    """
    ciphertext = fernet.encrypt(credential.token_json.encode("utf-8"))
    connection = (
        await session.scalars(
            select(ExternalConnection).where(
                ExternalConnection.tenant_id == tenant_id,
                ExternalConnection.user_id == user_id,
                ExternalConnection.provider == GOOGLE_PROVIDER,
                ExternalConnection.account_email == credential.account_email,
            )
        )
    ).one_or_none()
    if connection is None:
        connection = ExternalConnection(
            tenant_id=tenant_id,
            user_id=user_id,
            provider=GOOGLE_PROVIDER,
            account_email=credential.account_email,
            encrypted_credentials=ciphertext,
        )
        session.add(connection)
    else:
        connection.encrypted_credentials = ciphertext
        connection.revoked_at = None
    await session.flush()
    return connection


def load_credentials(connection: ExternalConnection, *, fernet: Fernet) -> str:
    """Decrypt and return a connection's stored OAuth token JSON (the serialized credentials).

    The connection row is already loaded by the caller, so this is a pure decrypt (no DB access).
    Build a live Google client from the result with :func:`build_live_service`.
    """
    return fernet.decrypt(connection.encrypted_credentials).decode("utf-8")


def build_live_service(
    connection: ExternalConnection, *, fernet: Fernet
) -> Any:  # pragma: no cover - live wiring
    """Production ``ServiceFactory``: decrypt the connection's token and build a live Google client.

    Wire it as ``service_factory=functools.partial(build_live_service, fernet=fernet)`` when calling
    :func:`read_busy` from the app. Kept out of the tested path because it constructs the untyped
    google-api-python-client.
    """
    credentials = credentials_from_token_json(load_credentials(connection, fernet=fernet))
    return build_service(credentials)


# --------------------------------------------------------------------------------------
# 2. BusyCache refresh (RF-12) -- replace this connection's cached window with a fresh pull.
# --------------------------------------------------------------------------------------


async def refresh_busy_cache(
    session: AsyncSession,
    *,
    connection: ExternalConnection,
    window: TimeInterval,
    now: datetime,
    service: Any,
) -> list[TimeInterval]:
    """Pull freebusy for ``connection`` over ``window`` and replace its cached rows (RF-12).

    ``service`` is an injected live Google client (built from ``build_service``); tests pass a fake.
    All existing ``BusyCache`` rows for the connection are dropped and the freshly-fetched busy
    blocks are written stamped ``fetched_at=now``. The connection's coverage stamp
    (``busy_synced_from/to`` = the fetched ``window``, ``busy_synced_at=now``) is set in the same
    flush so :func:`read_busy` can judge freshness by window coverage rather than a per-row age --
    and so a fetched window with ZERO busy blocks is representable as covered-and-fresh (not
    indistinguishable from "never synced"). Returns the busy intervals that were cached. The write
    is flushed, not committed -- the caller owns the transaction.
    """
    busy = query_busy(service, _DEFAULT_CALENDAR_ID, window)
    await session.execute(
        delete(BusyCache).where(
            BusyCache.tenant_id == connection.tenant_id,
            BusyCache.connection_id == connection.id,
        )
    )
    for interval in busy:
        session.add(
            BusyCache(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                start_at=interval.start,
                end_at=interval.end,
                fetched_at=now,
            )
        )
    connection.busy_synced_from = window.start
    connection.busy_synced_to = window.end
    connection.busy_synced_at = now
    await session.flush()
    return busy


# --------------------------------------------------------------------------------------
# 3. Busy read with TTL + RF-13 safe degradation -- what the slots engine (F1-04) consumes.
# --------------------------------------------------------------------------------------


async def read_busy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    host_user_id: uuid.UUID,
    query: BusyQuery,
    service_factory: ServiceFactory | None = None,
) -> BusyReadResult:
    """Return the host's external busy set over the window (window-aware TTL, RF-13 degradation).

    Freshness is judged by WINDOW COVERAGE, not by a per-connection ``fetched_at``: a cache filled
    for one window must never read as fresh for a different window (a cache of last week does not
    answer a query about next week -- reusing it is how a double-booking slips through). Let

    * ``covered``   = the connection's synced window fully contains ``query.window``
      (``busy_synced_from <= query.window.start`` and ``busy_synced_to >= query.window.end``); and
    * ``time_fresh`` = ``busy_synced_at`` is set and ``query.now - busy_synced_at <= query.ttl``.

    RF-13 decision table (see the module docstring for the full contract):

    * no connection for the host -> ``FRESH`` with empty busy (no external calendar = no busy).
    * ``covered and time_fresh`` -> ``FRESH`` from cache (rows intersecting the window), Google is
      NOT contacted -- an empty-but-covered window correctly returns ``busy=()``.
    * otherwise refresh via ``service_factory`` for ``query.window``:
        * refresh succeeds -> ``FRESH`` with the new data.
        * refresh fails and the PRIOR coverage fully contains the window (``covered``) -> ``STALE``
          with the last-known copy (degraded): it is complete for this window, so slots may still
          be offered.
        * refresh fails and coverage is partial/absent -> ``UNAVAILABLE`` (refuse to offer slots):
          partial coverage is never served as if complete, and "unknown" is never treated as free.

    ``query.now`` and ``query.ttl`` are injected so the TTL is deterministic in tests.
    ``service_factory`` builds the live client for a connection; when ``None`` (or Google is
    unreachable) the function serves only what the cache can prove -- it never treats "unknown" as
    "free".
    """
    connection = await _load_active_connection(session, tenant_id=tenant_id, user_id=host_user_id)
    if connection is None:
        return BusyReadResult(status=BusyStatus.FRESH, busy=())

    covered = _coverage_contains(connection, query.window)
    synced_at = (
        _as_utc(connection.busy_synced_at) if connection.busy_synced_at is not None else None
    )
    time_fresh = synced_at is not None and (query.now - synced_at) <= query.ttl

    cached = await _read_cache(session, connection=connection)
    last_known = tuple(_to_intervals(cached))

    if covered and time_fresh:
        return BusyReadResult(status=BusyStatus.FRESH, busy=_intersecting(last_known, query.window))

    if service_factory is None:
        return _degrade(
            connection,
            last_known,
            covered=covered,
            window=query.window,
            reason="no refresh factory",
        )

    try:
        service = service_factory(connection)
        busy = await refresh_busy_cache(
            session, connection=connection, window=query.window, now=query.now, service=service
        )
    except Exception:
        _logger.exception(
            "busy-cache refresh failed for connection %s (tenant %s)",
            connection.id,
            connection.tenant_id,
        )
        return _degrade(
            connection, last_known, covered=covered, window=query.window, reason="refresh failed"
        )

    return BusyReadResult(status=BusyStatus.FRESH, busy=tuple(busy))


def _degrade(
    connection: ExternalConnection,
    last_known: tuple[TimeInterval, ...],
    *,
    covered: bool,
    window: TimeInterval,
    reason: str,
) -> BusyReadResult:
    """Degrade a failed refresh: STALE only if the prior cache COVERS the window, else UNAVAILABLE.

    Serving the last-known copy is safe only when the prior coverage fully contains ``window`` --
    then the cache is complete for that window and can be offered as degraded (RF-13). Partial or
    absent coverage is refused (``UNAVAILABLE``): an uncovered stretch could hide a conflict, and
    treating it as free is exactly the double-booking this system must prevent.
    """
    if covered:
        _logger.warning(
            "serving degraded (last-known) busy for connection %s: %s", connection.id, reason
        )
        return BusyReadResult(status=BusyStatus.STALE, busy=_intersecting(last_known, window))
    _logger.error(
        "no complete coverage for connection %s and %s; marking host UNAVAILABLE",
        connection.id,
        reason,
    )
    return BusyReadResult(status=BusyStatus.UNAVAILABLE, busy=())


def _coverage_contains(connection: ExternalConnection, window: TimeInterval) -> bool:
    """True if the connection's last-synced busy window fully contains ``window`` (RF-12/13).

    ``NULL`` bounds (never synced) are not coverage. The stored bounds are normalized to UTC because
    SQLite drops tzinfo on round-trip and comparing a naive bound to the tz-aware ``window`` raises.
    """
    synced_from = connection.busy_synced_from
    synced_to = connection.busy_synced_to
    if synced_from is None or synced_to is None:
        return False
    return _as_utc(synced_from) <= window.start and _as_utc(synced_to) >= window.end


def _intersecting(
    intervals: tuple[TimeInterval, ...], window: TimeInterval
) -> tuple[TimeInterval, ...]:
    """The busy intervals that overlap ``window`` (half-open; touching endpoints do not overlap)."""
    return tuple(interval for interval in intervals if interval.overlaps(window))


async def _load_active_connection(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> ExternalConnection | None:
    """The host's active (not revoked) Google connection, scoped to the tenant."""
    return (
        await session.scalars(
            select(ExternalConnection).where(
                ExternalConnection.tenant_id == tenant_id,
                ExternalConnection.user_id == user_id,
                ExternalConnection.provider == GOOGLE_PROVIDER,
                ExternalConnection.revoked_at.is_(None),
            )
        )
    ).first()


async def _read_cache(session: AsyncSession, *, connection: ExternalConnection) -> list[BusyCache]:
    """All cached busy rows for a connection, tenant-scoped (belt-and-suspenders isolation)."""
    return list(
        (
            await session.scalars(
                select(BusyCache)
                .where(
                    BusyCache.tenant_id == connection.tenant_id,
                    BusyCache.connection_id == connection.id,
                )
                .order_by(BusyCache.start_at)
            )
        ).all()
    )


def _to_intervals(rows: list[BusyCache]) -> list[TimeInterval]:
    return [TimeInterval(start=_as_utc(row.start_at), end=_as_utc(row.end_at)) for row in rows]


def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive timestamp (SQLite drops tzinfo on round-trip; PostgreSQL keeps it)."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# --------------------------------------------------------------------------------------
# 4. Booking event lifecycle (RF-11) -- create / delete / reschedule the Google Meet event.
# --------------------------------------------------------------------------------------


async def create_event_for_booking(
    *,
    connection: ExternalConnection,
    request: MeetEventRequest,
    service: Any,
) -> tuple[str, str | None]:
    """Create the calendar event (with a Google Meet link) for a booking; return ``(id, meet_url)``.

    Does not touch the database -- the caller (F1-05) writes the returned ``external_event_id`` and
    ``meeting_url`` onto the ``Booking`` row inside its own transaction (single writer). A Google
    failure raises :class:`CalendarSyncError` so the caller can confirm the booking anyway and flag
    the sync for retry.
    """
    try:
        created = insert_event_with_meet(service, _DEFAULT_CALENDAR_ID, request)
    except Exception as exc:
        raise CalendarSyncError(
            f"failed to create Google event for connection {connection.id}"
        ) from exc
    return str(created["id"]), _extract_meet_url(created)


async def delete_event_for_booking(
    *,
    connection: ExternalConnection,
    external_event_id: str,
    service: Any,
) -> None:
    """Delete a booking's calendar event (cancel). Raises :class:`CalendarSyncError` on failure."""
    try:
        delete_event(service, _DEFAULT_CALENDAR_ID, external_event_id)
    except Exception as exc:
        raise CalendarSyncError(
            f"failed to delete Google event {external_event_id} for connection {connection.id}"
        ) from exc


async def reschedule_event_for_booking(
    *,
    connection: ExternalConnection,
    external_event_id: str,
    request: MeetEventRequest,
    service: Any,
) -> tuple[str, str | None]:
    """Reschedule by removing the old event and inserting a new one (fresh Meet link).

    Delete-and-reinsert (rather than ``events.patch``) keeps the code path identical to create and
    guarantees a clean conference for the new time. Returns the new ``(id, meet_url)``; any Google
    failure raises :class:`CalendarSyncError` for the caller to confirm-and-retry.
    """
    try:
        delete_event(service, _DEFAULT_CALENDAR_ID, external_event_id)
        created = insert_event_with_meet(service, _DEFAULT_CALENDAR_ID, request)
    except Exception as exc:
        raise CalendarSyncError(
            f"failed to reschedule Google event {external_event_id} for connection {connection.id}"
        ) from exc
    return str(created["id"]), _extract_meet_url(created)


def _extract_meet_url(created: dict[str, Any]) -> str | None:
    """Pull the Meet join URL from a created event (``hangoutLink`` or a video conference entry)."""
    hangout = created.get("hangoutLink")
    if isinstance(hangout, str) and hangout:
        return hangout
    conference: dict[str, Any] = created.get("conferenceData") or {}
    for entry in conference.get("entryPoints") or []:
        if isinstance(entry, dict) and entry.get("entryPointType") == "video":
            uri = entry.get("uri")
            if isinstance(uri, str) and uri:
                return uri
    return None


__all__ = [
    "BusyQuery",
    "BusyReadResult",
    "BusyStatus",
    "CalendarSyncError",
    "GoogleCredential",
    "ServiceFactory",
    "build_live_service",
    "create_event_for_booking",
    "delete_event_for_booking",
    "load_credentials",
    "read_busy",
    "refresh_busy_cache",
    "reschedule_event_for_booking",
    "store_google_connection",
]
