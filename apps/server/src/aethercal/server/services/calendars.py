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
from a covered + time-fresh cache or a successful refresh (an empty-but-covered window is FRESH with
no busy). ``STALE`` = a refresh failed and the prior coverage fully contained the window, so we serve
the last-known (complete-for-this-window) copy (``is_degraded``); slots may still be offered.
``UNAVAILABLE`` = a refresh failed with partial or absent coverage and we cannot reach Google
(``not is_available``) -- the slots engine MUST refuse to offer this host's slots rather than serve
incomplete data as complete and risk a double-booking.

==NO CALENDAR IS NOT A BROKEN CALENDAR (RF-13 vs RNF-9).== These two look alike (neither has usable
busy data) and must never be conflated:

* **The host has NO connected calendar at all** -- no ``ExternalConnection`` row. There is no
  external busy set to miss, because there is no external calendar: ``FRESH`` with an empty busy
  set, and their slots are offered normally (only INTERNAL bookings block them). This is the
  self-hoster who never linked a Google account, and RNF-9 ("no core function depends on a
  proprietary service") means the product must work perfectly for them. Refusing their slots because
  "we could not read a calendar" would leave them with ZERO bookable slots -- dead on arrival.
* **The host HAS a connected calendar we cannot read** -- a connection exists, the refresh failed
  and no cached copy covers the window: ``UNAVAILABLE``. Here there IS an external busy set and we
  do not know it, so an offered slot could double-book a real meeting. Offer nothing.

The difference is the EXISTENCE OF THE CONNECTION, and it is decided before any cache/TTL logic
runs (see :func:`read_busy`).

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
from aethercal.server.db.models import BusyCache, ExternalCalendarLink, ExternalConnection
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
# The calendar a connection uses when the operator has linked NONE explicitly: the account's own
# default. It is a FALLBACK, not the policy — the calendar is configured per connection through the
# ``ExternalCalendarLink`` rows (which is how a host books into a dedicated secondary calendar
# instead of their primary). Until this wave it was a hard-coded constant, and the link table was
# therefore written by nobody and read by nobody.
DEFAULT_CALENDAR_ID = "primary"

# The HTTP statuses that mean "the event Google was asked to delete is already gone". Deleting an
# absent event is a SUCCESS: the desired state (no event) holds. Treating it as a failure makes a
# retried cancellation — or the delete half of a reschedule that crashed after it — fail forever.
_ALREADY_GONE_STATUSES = frozenset({404, 410})

# Builds a live Google ``service`` for a connection. Injected into ``read_busy`` so the refresh path
# is fully faked offline; the production factory is ``build_live_service`` (bound via partial).
ServiceFactory = Callable[[ExternalConnection], Any]


class CalendarSyncError(RuntimeError):
    """A Google Calendar mutation (create/delete/reschedule an event) failed.

    The booking caller (F1-05) catches this to confirm the booking anyway, flag the sync for retry,
    and log -- booking success must never hard-depend on Google being reachable (RF-13 for writes).
    """


class CalendarTargetMissingError(RuntimeError):
    """A booking EXPECTED the host to have a connected calendar, and none was found.

    The distinction this class exists for: "this host has no calendar" (benign — the self-hoster,
    nothing to sync, no intent is ever enqueued) is NOT the same as "the host HAD a calendar when
    the booking was taken and the lookup now finds none" (a real failure — the guest is confirmed,
    the host's calendar has no event, and without this error nobody would ever find out). The first
    never reaches the outbox; the second raises here, so the intent retries, dead-letters, and shows
    up in the ``dead`` backlog with a loud log line instead of passing as a delivered no-op.
    """


class AmbiguousCalendarTargetError(RuntimeError):
    """The host's calendar configuration does not name ONE calendar to write bookings into.

    Raised when a host has several active connections (or several linked calendars) and none is
    flagged ``is_booking_target``, or when several are. The alternative — taking the first row — is
    what the old ``.first()`` did: it wrote the event into an arbitrary calendar and reported
    nothing. Refusing surfaces the misconfiguration as a retrying/dead-lettered outbox intent an
    operator can see, instead of a booking that quietly never reached the host's calendar.
    """


@dataclass(frozen=True)
class CalendarTarget:
    """The exact calendar a booking's event is written to (or was written to): connection + id."""

    connection: ExternalConnection
    calendar_id: str


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


async def load_active_connections(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[ExternalConnection]:
    """ALL of the host's active (not revoked) Google connections, tenant-scoped, oldest first.

    Deliberately plural. The predecessor of this function ended in ``.first()``, so a host with two
    connected accounts had one of them silently ignored — and an ignored calendar is an ignored busy
    set, which is a double-booking waiting to happen. Every read path unions the lot.
    """
    return list(
        (
            await session.scalars(
                select(ExternalConnection)
                .where(
                    ExternalConnection.tenant_id == tenant_id,
                    ExternalConnection.user_id == user_id,
                    ExternalConnection.provider == GOOGLE_PROVIDER,
                    ExternalConnection.revoked_at.is_(None),
                )
                .order_by(ExternalConnection.created_at, ExternalConnection.id)
            )
        ).all()
    )


async def _links(
    session: AsyncSession, *, connection: ExternalConnection
) -> list[ExternalCalendarLink]:
    """The connection's linked calendars, tenant-scoped, in a stable order."""
    return list(
        (
            await session.scalars(
                select(ExternalCalendarLink)
                .where(
                    ExternalCalendarLink.tenant_id == connection.tenant_id,
                    ExternalCalendarLink.connection_id == connection.id,
                )
                .order_by(ExternalCalendarLink.created_at, ExternalCalendarLink.id)
            )
        ).all()
    )


async def busy_calendar_ids(
    session: AsyncSession, *, connection: ExternalConnection
) -> list[str]:
    """The calendars of ``connection`` whose freebusy counts toward the host's busy set (RF-12).

    No links at all → the account's default calendar (the zero-config path, and what the hard-coded
    constant used to do for everyone). With links, only those flagged ``busy`` are read: an operator
    who links a calendar and opts it out has said so explicitly, which is not the same thing as the
    code quietly never looking at it.
    """
    links = await _links(session, connection=connection)
    if not links:
        return [DEFAULT_CALENDAR_ID]
    return [link.external_calendar_id for link in links if link.busy]


async def resolve_calendar_target(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> CalendarTarget | None:
    """The ONE calendar the host's bookings are written into — or a loud refusal (RF-11/RF-30).

    ``None`` means the host has no connected calendar: there is genuinely nothing to sync. Otherwise
    the target must be unambiguous:

    * exactly one link flagged ``is_booking_target`` (across every active connection) → that one;
    * no links at all and exactly one connection → that account's default calendar (zero-config);
    * anything else (several connections, or several linked calendars, with no designated target —
      or several designated) → :class:`AmbiguousCalendarTargetError`.

    The refusal is the point. Guessing here writes a real client's meeting into an arbitrary
    calendar and reports success, which is exactly the failure this system exists to prevent; a
    raise turns it into a retrying, then dead-lettered, outbox intent an operator can actually see.
    """
    connections = await load_active_connections(session, tenant_id=tenant_id, user_id=user_id)
    if not connections:
        return None

    designated: list[CalendarTarget] = []
    linked_any = False
    for connection in connections:
        links = await _links(session, connection=connection)
        linked_any = linked_any or bool(links)
        designated.extend(
            CalendarTarget(connection=connection, calendar_id=link.external_calendar_id)
            for link in links
            if link.is_booking_target
        )

    if len(designated) == 1:
        return designated[0]
    if len(designated) > 1:
        raise AmbiguousCalendarTargetError(
            f"host {user_id} has {len(designated)} calendars flagged as the booking target; "
            "exactly one must be"
        )
    if len(connections) == 1 and not linked_any:
        return CalendarTarget(connection=connections[0], calendar_id=DEFAULT_CALENDAR_ID)
    raise AmbiguousCalendarTargetError(
        f"host {user_id} has {len(connections)} active connection(s) and linked calendars but no "
        "calendar flagged as the booking target; designate one (is_booking_target)"
    )


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
    Every calendar of the connection flagged ``busy`` is queried and their blocks are UNIONED (a
    host is busy if ANY of their calendars is). All existing ``BusyCache`` rows for the connection
    are dropped and the freshly-fetched busy blocks are written stamped ``fetched_at=now``. The
    connection's coverage stamp (``busy_synced_from/to`` = the fetched ``window``,
    ``busy_synced_at=now``) is set in the same flush so :func:`read_busy` can judge freshness by
    window coverage rather than a per-row age -- and so a fetched window with ZERO busy blocks is
    representable as covered-and-fresh (not indistinguishable from "never synced"). Returns the busy
    intervals that were cached. The write is flushed, not committed -- the caller owns the
    transaction.
    """
    busy: list[TimeInterval] = []
    for calendar_id in await busy_calendar_ids(session, connection=connection):
        busy.extend(query_busy(service, calendar_id, window))
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

    MULTI-CONNECTION (RF-30): a host may have several connected accounts, and they are ALL read --
    their busy sets are unioned. The predecessor took the first row and dropped the rest, which
    offered slots the host was already booked in. The statuses combine fail-closed: ONE connection
    we cannot establish makes the whole host ``UNAVAILABLE`` (serving the readable calendars alone
    would present incomplete data as complete); otherwise one degraded copy makes the result
    ``STALE``.
    """
    connections = await load_active_connections(session, tenant_id=tenant_id, user_id=host_user_id)
    if not connections:
        # NO CONNECTED CALENDAR — not a broken one (RNF-9). There is no external busy set to be
        # ignorant of, so this is FRESH-and-empty and the host's slots are offered normally (only
        # their internal bookings block them). This is the self-hoster who never linked a Google
        # account: reading RF-13 literally here would offer them zero slots forever. The refusal
        # below (UNAVAILABLE) is reserved for a calendar that EXISTS and cannot be read.
        return BusyReadResult(status=BusyStatus.FRESH, busy=())

    busy: list[TimeInterval] = []
    degraded = False
    for connection in connections:
        result = await _read_connection_busy(
            session, connection=connection, query=query, service_factory=service_factory
        )
        if result.status is BusyStatus.UNAVAILABLE:
            return BusyReadResult(status=BusyStatus.UNAVAILABLE, busy=())
        degraded = degraded or result.is_degraded
        busy.extend(result.busy)

    status = BusyStatus.STALE if degraded else BusyStatus.FRESH
    return BusyReadResult(status=status, busy=tuple(busy))


async def _read_connection_busy(
    session: AsyncSession,
    *,
    connection: ExternalConnection,
    query: BusyQuery,
    service_factory: ServiceFactory | None,
) -> BusyReadResult:
    """One connection's busy set over the window, with the RF-12/13 freshness + degradation rules.

    The per-connection half of :func:`read_busy` (which unions these across every active connection
    of the host); the decision table lives in that docstring and the module header.
    """
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
    calendar_id: str,
    request: MeetEventRequest,
    service: Any,
) -> tuple[str, str | None]:
    """Create the calendar event (with a Google Meet link) in ``calendar_id``; ``(id, meet_url)``.

    PRIMITIVES, not a ``CalendarTarget``, and that is load-bearing: this runs with NO session open
    (the outbox releases its connection before any network call, R8), so an ORM object reaching here
    would be detached — and touching one of its attributes, even just to name it in an error message,
    raises ``DetachedInstanceError`` INSTEAD of the :class:`CalendarSyncError` the retry logic reads.
    A resolver builds the target while a session is alive; only its primitives cross this line.

    Does not touch the database -- the caller writes the returned ``external_event_id`` /
    ``meeting_url``, and the calendar they landed in, onto the ``Booking`` row inside its own
    transaction. A Google failure raises :class:`CalendarSyncError` so the intent retries.
    """
    try:
        created = insert_event_with_meet(service, calendar_id, request)
    except Exception as exc:
        raise CalendarSyncError(
            f"failed to create Google event in calendar {calendar_id}"
        ) from exc
    return str(created["id"]), _extract_meet_url(created)


def _is_already_gone(exc: Exception) -> bool:
    """True when Google's error says the event is not there any more (404 / 410).

    googleapiclient raises ``HttpError``, whose ``resp.status`` (newer builds also expose
    ``status_code``) carries the code. Read defensively rather than importing the untyped error
    class: the seam around the SDK stays intact, and a fake in the tests can model it exactly.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    return isinstance(status, int) and status in _ALREADY_GONE_STATUSES


async def delete_event_for_booking(
    *,
    calendar_id: str,
    external_event_id: str,
    service: Any,
) -> None:
    """Delete a booking's calendar event from ``calendar_id`` (cancel). IDEMPOTENT.

    An event Google no longer has (404 / 410) is a SUCCESS: the desired end state — no event — is
    exactly what holds, and the outbox retries at-least-once, so a re-drained cancellation (or the
    delete half of a reschedule that crashed right after it) must not fail forever and dead-letter
    over an event it already removed. Any other failure raises :class:`CalendarSyncError` and the
    intent retries.
    """
    try:
        delete_event(service, calendar_id, external_event_id)
    except Exception as exc:
        if _is_already_gone(exc):
            _logger.info(
                "Google event %s already absent from calendar %s; delete is a no-op",
                external_event_id,
                calendar_id,
            )
            return
        raise CalendarSyncError(
            f"failed to delete Google event {external_event_id} from calendar {calendar_id}"
        ) from exc


async def reschedule_event_for_booking(  # noqa: PLR0913 - source/target + their clients + event
    *,
    source_calendar_id: str,
    source_service: Any,
    target_calendar_id: str,
    target_service: Any,
    external_event_id: str,
    request: MeetEventRequest,
) -> tuple[str, str | None]:
    """Move a booking's event: delete it where it LIVES (``source``), create it where it BELONGS.

    ``source`` is the calendar the event was actually written to (persisted on the booking), which
    is not necessarily the host's currently-configured ``target`` — a host who re-designates their
    booking calendar between the confirmation and the reschedule would otherwise leave the old event
    orphaned in the old calendar. They are usually the same, and the caller then passes the same
    client twice.

    Delete-and-reinsert (rather than ``events.patch``) keeps the code path identical to create and
    guarantees a clean conference for the new time. Returns the new ``(id, meet_url)``; a Google
    failure raises :class:`CalendarSyncError` for the intent to retry (the delete is idempotent, so
    a retry after a partial move re-deletes harmlessly and re-creates).
    """
    await delete_event_for_booking(
        calendar_id=source_calendar_id,
        external_event_id=external_event_id,
        service=source_service,
    )
    try:
        created = insert_event_with_meet(target_service, target_calendar_id, request)
    except Exception as exc:
        raise CalendarSyncError(
            f"failed to re-create Google event {external_event_id} in calendar "
            f"{target_calendar_id}"
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
    "DEFAULT_CALENDAR_ID",
    "GOOGLE_PROVIDER",
    "AmbiguousCalendarTargetError",
    "BusyQuery",
    "BusyReadResult",
    "BusyStatus",
    "CalendarSyncError",
    "CalendarTarget",
    "CalendarTargetMissingError",
    "GoogleCredential",
    "ServiceFactory",
    "build_live_service",
    "busy_calendar_ids",
    "create_event_for_booking",
    "delete_event_for_booking",
    "load_active_connections",
    "load_credentials",
    "read_busy",
    "refresh_busy_cache",
    "reschedule_event_for_booking",
    "resolve_calendar_target",
    "store_google_connection",
]
