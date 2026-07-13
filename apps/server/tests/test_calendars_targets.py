"""The calendar TARGET resolver and the multi-connection busy read (RF-11/RF-12/RF-13, RF-30).

Two silent no-ops die here, and every test below asserts the EFFECTIVE state (which calendar was
actually called, which busy blocks actually came back), never the apparent one:

* ``_load_active_connection(...).first()`` — a host with two active connections had one of them
  silently ignored. Ignoring a calendar means ignoring its busy blocks, which is a double-booking.
  Reads now union EVERY active connection, and the write path REFUSES to guess which one owns the
  bookings (a loud :class:`AmbiguousCalendarTargetError`, not an arbitrary pick).
* ``_DEFAULT_CALENDAR_ID = "primary"`` — a constant, so the ``external_calendar_links`` rows were
  written by nobody and read by nobody. The calendar is now configurable per connection, which is
  what lets a host book into a DEDICATED secondary calendar instead of the account's ``primary``.

The Google client is a fake keyed BY CALENDAR ID: a test that expects the dedicated calendar to be
used fails loudly if the code silently falls back to ``"primary"``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import TimeInterval
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    BusyCache,
    ExternalCalendarLink,
    ExternalConnection,
    Tenant,
    User,
)
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.services.calendars import (
    DEFAULT_CALENDAR_ID,
    AmbiguousCalendarTargetError,
    BusyQuery,
    BusyStatus,
    CalendarSyncError,
    CalendarTarget,
    GoogleCredential,
    busy_calendar_ids,
    create_event_for_booking,
    delete_event_for_booking,
    load_active_connections,
    read_busy,
    refresh_busy_cache,
    reschedule_event_for_booking,
    resolve_calendar_target,
    store_google_connection,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# A fake Google service keyed by calendar id — so "which calendar was used" is observable.
# --------------------------------------------------------------------------------------


class _Execute:
    def __init__(self, result: Any, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def execute(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._result


class _GoneError(Exception):
    """Mimics googleapiclient's HttpError for an event Google no longer has (410 Gone)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class _Freebusy:
    def __init__(self, owner: FakeGoogle) -> None:
        self._owner = owner

    def query(self, *, body: Any) -> _Execute:
        if self._owner.freebusy_error is not None:
            return _Execute(None, self._owner.freebusy_error)
        requested = [item["id"] for item in body["items"]]
        self._owner.freebusy_calls.extend(requested)
        calendars = {
            calendar_id: {
                "busy": [
                    {"start": start, "end": end}
                    for start, end in self._owner.busy_by_calendar.get(calendar_id, [])
                ]
            }
            for calendar_id in requested
        }
        return _Execute({"calendars": calendars})


class _Events:
    def __init__(self, owner: FakeGoogle) -> None:
        self._owner = owner

    def insert(
        self, *, calendarId: str, body: Any, conferenceDataVersion: int, sendUpdates: str
    ) -> _Execute:
        event_id = f"evt-{len(self._owner.created) + 1}"
        self._owner.created.append((calendarId, event_id))
        return _Execute({"id": event_id, "hangoutLink": f"https://meet.example/{event_id}"})

    def delete(self, *, calendarId: str, eventId: str, sendUpdates: str) -> _Execute:
        self._owner.deleted.append((calendarId, eventId))
        return _Execute(None, self._owner.delete_error)


class FakeGoogle:
    """Records the calendar id of every call, so a silent fallback to ``"primary"`` is detectable."""

    def __init__(
        self,
        *,
        busy_by_calendar: dict[str, list[tuple[str, str]]] | None = None,
        freebusy_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.busy_by_calendar = busy_by_calendar or {}
        self.freebusy_error = freebusy_error
        self.delete_error = delete_error
        self.freebusy_calls: list[str] = []
        self.created: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def freebusy(self) -> _Freebusy:
        return _Freebusy(self)

    def events(self) -> _Events:
        return _Events(self)


# --------------------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------------------


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


async def _host(session: AsyncSession, tenant: Tenant) -> User:
    return (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()


async def _connect(
    session: AsyncSession,
    tenant: Tenant,
    *,
    fernet: Fernet,
    account_email: str = "host@agency.test",
    user: User | None = None,
) -> ExternalConnection:
    host = user or await _host(session, tenant)
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email=account_email, token_json='{"token": "at"}'),
        fernet=fernet,
    )
    await session.flush()
    return connection


async def _link(
    session: AsyncSession,
    connection: ExternalConnection,
    *,
    calendar_id: str,
    busy: bool = True,
    is_booking_target: bool = False,
) -> ExternalCalendarLink:
    row = ExternalCalendarLink(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        external_calendar_id=calendar_id,
        busy=busy,
        is_booking_target=is_booking_target,
    )
    session.add(row)
    await session.flush()
    return row


async def _cover(
    session: AsyncSession, connection: ExternalConnection, *, at: datetime = NOW
) -> None:
    connection.busy_synced_from = NOW
    connection.busy_synced_to = NOW + timedelta(days=7)
    connection.busy_synced_at = at
    await session.flush()


def _query(now: datetime = NOW, *, ttl: timedelta = timedelta(minutes=5)) -> BusyQuery:
    return BusyQuery(window=TimeInterval(start=now, end=now + timedelta(days=7)), now=now, ttl=ttl)


def _request() -> MeetEventRequest:
    return MeetEventRequest(
        summary="Discovery call",
        start=NOW + timedelta(days=1),
        end=NOW + timedelta(days=1, minutes=30),
        timezone="UTC",
        guest_email="lead@example.com",
    )


# --------------------------------------------------------------------------------------
# 1. resolve_calendar_target — deterministic, or a LOUD refusal. Never an arbitrary pick.
# --------------------------------------------------------------------------------------


async def test_no_connection_resolves_to_no_target(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)

    target = await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)

    assert target is None  # a host with no connected calendar has genuinely nothing to sync


async def test_single_connection_without_links_targets_the_account_default(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)

    target = await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)

    # Zero-config still works: one connection, no links -> the account's default calendar.
    assert target == CalendarTarget(connection=connection, calendar_id=DEFAULT_CALENDAR_ID)


async def test_a_designated_link_targets_that_calendar_not_primary(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    await _link(
        sqlite_session,
        connection,
        calendar_id="dedicated@group.calendar.google.com",
        is_booking_target=True,
    )

    target = await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)

    assert target is not None
    # The whole point of the agency credential rule: bookings land in a DEDICATED secondary
    # calendar, never in the account's primary.
    assert target.calendar_id == "dedicated@group.calendar.google.com"
    assert target.calendar_id != DEFAULT_CALENDAR_ID


async def test_links_without_a_designated_target_refuse_loudly(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    await _link(sqlite_session, connection, calendar_id="team@group.calendar.google.com")
    await _link(sqlite_session, connection, calendar_id="ops@group.calendar.google.com")

    # Calendars are configured, but none is flagged as the booking target. Falling back to
    # "primary" would silently write into the WRONG calendar — refuse instead.
    with pytest.raises(AmbiguousCalendarTargetError):
        await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)


async def test_two_active_connections_without_a_target_refuse_loudly(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    await _connect(sqlite_session, tenant, fernet=fernet, account_email="work@agency.test")
    await _connect(sqlite_session, tenant, fernet=fernet, account_email="ops@agency.test")

    # This is the `.first()` bug: the old code picked whichever row came back first and ignored the
    # other — a real booking would land in an arbitrary account with no error anywhere.
    with pytest.raises(AmbiguousCalendarTargetError):
        await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)


async def test_two_connections_with_one_designated_target_resolve_to_it(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    await _connect(sqlite_session, tenant, fernet=fernet, account_email="work@agency.test")
    second = await _connect(sqlite_session, tenant, fernet=fernet, account_email="ops@agency.test")
    await _link(
        sqlite_session,
        second,
        calendar_id="bookings@group.calendar.google.com",
        is_booking_target=True,
    )

    target = await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)

    assert target == CalendarTarget(
        connection=second, calendar_id="bookings@group.calendar.google.com"
    )


async def test_revoked_connections_are_not_candidates(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    live = await _connect(sqlite_session, tenant, fernet=fernet, account_email="live@agency.test")
    revoked = await _connect(sqlite_session, tenant, fernet=fernet, account_email="old@agency.test")
    revoked.revoked_at = NOW
    await sqlite_session.flush()

    assert await load_active_connections(sqlite_session, tenant_id=tenant.id, user_id=host.id) == [
        live
    ]
    # And with the revoked one out of the way the remaining single connection is unambiguous.
    target = await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id)
    assert target is not None and target.connection.id == live.id


# --------------------------------------------------------------------------------------
# 2. busy_calendar_ids — which calendars of a connection feed the busy set.
# --------------------------------------------------------------------------------------


async def test_a_connection_without_links_reads_the_account_default(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)

    assert await busy_calendar_ids(sqlite_session, connection=connection) == [DEFAULT_CALENDAR_ID]


async def test_links_flagged_busy_are_read_and_opted_out_ones_are_not(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    await _link(sqlite_session, connection, calendar_id="work@cal", busy=True)
    await _link(sqlite_session, connection, calendar_id="holidays@cal", busy=False)

    assert await busy_calendar_ids(sqlite_session, connection=connection) == ["work@cal"]


async def test_refresh_pulls_freebusy_from_every_busy_calendar_of_the_connection(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    await _link(sqlite_session, connection, calendar_id="work@cal", is_booking_target=True)
    await _link(sqlite_session, connection, calendar_id="side@cal")
    await _link(sqlite_session, connection, calendar_id="ignored@cal", busy=False)
    google = FakeGoogle(
        busy_by_calendar={
            "work@cal": [("2026-07-11T09:00:00Z", "2026-07-11T10:00:00Z")],
            "side@cal": [("2026-07-12T14:00:00Z", "2026-07-12T15:00:00Z")],
            "ignored@cal": [("2026-07-13T09:00:00Z", "2026-07-13T23:00:00Z")],
        }
    )

    written = await refresh_busy_cache(
        sqlite_session, connection=connection, window=_query().window, now=NOW, service=google
    )

    assert sorted(google.freebusy_calls) == ["side@cal", "work@cal"]  # the opted-out one is skipped
    assert len(written) == 2  # the union of both busy calendars is cached
    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert len(rows) == 2


# --------------------------------------------------------------------------------------
# 3. read_busy across MANY connections — the union, and fail-closed degradation.
# --------------------------------------------------------------------------------------


async def test_busy_of_every_active_connection_is_unioned(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    first = await _connect(sqlite_session, tenant, fernet=fernet, account_email="a@agency.test")
    second = await _connect(sqlite_session, tenant, fernet=fernet, account_email="b@agency.test")
    a_busy = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    b_busy = (NOW + timedelta(hours=5), NOW + timedelta(hours=6))
    for connection, (start, end) in ((first, a_busy), (second, b_busy)):
        sqlite_session.add(
            BusyCache(
                tenant_id=tenant.id,
                connection_id=connection.id,
                start_at=start,
                end_at=end,
                fetched_at=NOW,
            )
        )
        await _cover(sqlite_session, connection)

    result = await read_busy(
        sqlite_session, tenant_id=tenant.id, host_user_id=host.id, query=_query()
    )

    assert result.status is BusyStatus.FRESH
    # The second connection's busy is NOT silently dropped — the `.first()` bug offered a slot the
    # host was already booked in.
    assert set(result.busy) == {
        TimeInterval(start=a_busy[0], end=a_busy[1]),
        TimeInterval(start=b_busy[0], end=b_busy[1]),
    }


async def test_one_unreadable_connection_makes_the_whole_host_unavailable(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    healthy = await _connect(sqlite_session, tenant, fernet=fernet, account_email="a@agency.test")
    broken = await _connect(sqlite_session, tenant, fernet=fernet, account_email="b@agency.test")
    await _cover(sqlite_session, healthy)  # covered + fresh, empty busy
    # `broken` has never synced and Google is unreachable for it.

    def factory(connection: ExternalConnection) -> Any:
        if connection.id == broken.id:
            return FakeGoogle(freebusy_error=RuntimeError("google unreachable"))
        return FakeGoogle()

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=host.id,
        query=_query(),
        service_factory=factory,
    )

    # RF-13, fail-closed: one calendar we cannot establish is enough to refuse the host's slots.
    # Serving only the healthy calendar's busy would present incomplete data as complete.
    assert result.status is BusyStatus.UNAVAILABLE
    assert not result.is_available
    assert result.busy == ()


# --------------------------------------------------------------------------------------
# 4. The event lifecycle writes to the TARGET's calendar — and delete is idempotent.
# --------------------------------------------------------------------------------------


async def test_create_writes_into_the_target_calendar(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    google = FakeGoogle()
    target = CalendarTarget(connection=connection, calendar_id="dedicated@cal")

    event_id, meeting_url = await create_event_for_booking(
        calendar_id=target.calendar_id, request=_request(), service=google
    )

    assert google.created == [("dedicated@cal", event_id)]  # not "primary"
    assert meeting_url == f"https://meet.example/{event_id}"


async def test_delete_removes_the_event_from_the_calendar_it_lives_in(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    google = FakeGoogle()

    await delete_event_for_booking(
        calendar_id="dedicated@cal", external_event_id="evt-1", service=google
    )

    assert google.deleted == [("dedicated@cal", "evt-1")]


async def test_deleting_an_event_google_no_longer_has_is_a_success_not_a_retry_loop(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    """A delete is idempotent. Google answers 410 Gone for an event it already removed; treating
    that as a failure would make a retried cancel (or the delete half of a reschedule that crashed
    after it) fail forever and dead-letter — the effect DID happen, the event IS gone."""
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    google = FakeGoogle(delete_error=_GoneError(410))

    await delete_event_for_booking(
        calendar_id="dedicated@cal", external_event_id="evt-gone", service=google
    )  # must not raise


async def test_a_real_delete_failure_still_raises(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    google = FakeGoogle(delete_error=RuntimeError("google is down"))

    with pytest.raises(CalendarSyncError):
        await delete_event_for_booking(
            calendar_id="dedicated@cal", external_event_id="evt-1", service=google
        )


async def test_reschedule_deletes_at_the_source_and_creates_at_the_target(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    """A moved booking-target must not orphan the old event: the delete goes to the calendar the
    event actually lives in (source), the create to the currently configured one (target)."""
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    google = FakeGoogle()
    source = CalendarTarget(connection=connection, calendar_id="old@cal")
    target = CalendarTarget(connection=connection, calendar_id="new@cal")

    event_id, _url = await reschedule_event_for_booking(
        source_calendar_id=source.calendar_id,
        source_service=google,
        target_calendar_id=target.calendar_id,
        target_service=google,
        external_event_id="evt-old",
        request=_request(),
    )

    assert google.deleted == [("old@cal", "evt-old")]
    assert google.created == [("new@cal", event_id)]


async def test_target_resolution_is_tenant_isolated(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant_a = await tenant_factory(sqlite_session, slug="a", email="a@host.test")
    tenant_b = await tenant_factory(sqlite_session, slug="b", email="b@host.test")
    host_a = await _host(sqlite_session, tenant_a)
    await _connect(sqlite_session, tenant_b, fernet=fernet, account_email="b@agency.test")

    # Tenant B's connection must never resolve as tenant A's host target.
    assert (
        await resolve_calendar_target(sqlite_session, tenant_id=tenant_a.id, user_id=host_a.id)
        is None
    )
    assert (
        await load_active_connections(sqlite_session, tenant_id=tenant_a.id, user_id=uuid.uuid4())
        == []
    )
