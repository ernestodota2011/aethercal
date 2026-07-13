"""Offline tests for the Google-calendar service layer (F1-07, RF-11/12/13).

Every test runs against the in-memory ``sqlite_session`` with a FAKE Google ``service`` stub, so the
credential encryption, the BusyCache TTL + safe-degradation logic (RF-13), and the booking event
lifecycle are all exercised without a network or live OAuth. The stub mimics the exact call chains
the live seam (``integrations/google/calendar.py``) makes:
``freebusy().query(body=...).execute()`` / ``events().insert(...).execute()`` /
``events().delete(...).execute()``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import TimeInterval
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import BusyCache, ExternalConnection, Tenant, User
from aethercal.server.integrations.google.parse import MeetEventRequest
from aethercal.server.services.calendars import (
    DEFAULT_CALENDAR_ID,
    BusyQuery,
    BusyStatus,
    CalendarSyncError,
    GoogleCredential,
    create_event_for_booking,
    delete_event_for_booking,
    load_credentials,
    read_busy,
    refresh_busy_cache,
    reschedule_event_for_booking,
    store_google_connection,
)

# --------------------------------------------------------------------------------------
# A fake Google Calendar service stub -- canned dicts down the same chains calendar.py calls.
# The camelCase keyword names mirror the google-api-python-client so the live wrappers bind.
# --------------------------------------------------------------------------------------


class _FakeExecute:
    def __init__(self, result: Any, error: Exception | None) -> None:
        self._result = result
        self._error = error

    def execute(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._result


class _FakeFreebusy:
    def __init__(self, response: Any, error: Exception | None) -> None:
        self._response = response
        self._error = error

    def query(self, *, body: Any) -> _FakeExecute:
        return _FakeExecute(self._response, self._error)


class _FakeEvents:
    def __init__(
        self,
        insert_result: Any,
        insert_error: Exception | None,
        delete_error: Exception | None,
    ) -> None:
        self._insert_result = insert_result
        self._insert_error = insert_error
        self._delete_error = delete_error
        self.deleted: list[str] = []

    def insert(
        self, *, calendarId: str, body: Any, conferenceDataVersion: int, sendUpdates: str
    ) -> _FakeExecute:
        return _FakeExecute(self._insert_result, self._insert_error)

    def delete(self, *, calendarId: str, eventId: str, sendUpdates: str) -> _FakeExecute:
        self.deleted.append(eventId)
        return _FakeExecute(None, self._delete_error)


class FakeGoogleService:
    """Returns canned freebusy/events results (or raises, to model an unreachable Google)."""

    def __init__(
        self,
        *,
        freebusy_response: Any = None,
        freebusy_error: Exception | None = None,
        insert_result: Any = None,
        insert_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self._freebusy_response = freebusy_response
        self._freebusy_error = freebusy_error
        self._events = _FakeEvents(insert_result, insert_error, delete_error)

    def freebusy(self) -> _FakeFreebusy:
        return _FakeFreebusy(self._freebusy_response, self._freebusy_error)

    def events(self) -> _FakeEvents:
        return self._events


def _freebusy(blocks: list[tuple[str, str]]) -> dict[str, Any]:
    return {"calendars": {"primary": {"busy": [{"start": s, "end": e} for s, e in blocks]}}}


_INSERT_RESULT = {
    "id": "evt-123",
    "htmlLink": "https://calendar.google.com/event?eid=evt-123",
    "hangoutLink": "https://meet.google.com/abc-defg-hij",
    "conferenceData": {
        "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]
    },
}


def _as_utc(moment: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat a naive timestamp as the UTC it was stored as."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _event(now: datetime) -> MeetEventRequest:
    return MeetEventRequest(
        summary="Discovery call",
        start=now + timedelta(days=1),
        end=now + timedelta(days=1, minutes=30),
        timezone="UTC",
        guest_email="lead@example.com",
    )


# --------------------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------------------


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


async def _first_user(session: AsyncSession, tenant: Tenant) -> User:
    return (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()


async def _connect(
    session: AsyncSession,
    tenant: Tenant,
    *,
    fernet: Fernet,
    account_email: str = "host@gmail.com",
    token_json: str = '{"token": "at", "refresh_token": "rt"}',
) -> ExternalConnection:
    user = await _first_user(session, tenant)
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        credential=GoogleCredential(account_email=account_email, token_json=token_json),
        fernet=fernet,
    )
    await session.flush()
    return connection


async def _seed_busy(
    session: AsyncSession,
    connection: ExternalConnection,
    *,
    fetched_at: datetime,
    blocks: list[tuple[datetime, datetime]],
) -> None:
    for start, end in blocks:
        session.add(
            BusyCache(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                start_at=start,
                end_at=end,
                fetched_at=fetched_at,
            )
        )
    await session.flush()


async def _set_coverage(
    session: AsyncSession,
    connection: ExternalConnection,
    *,
    synced_from: datetime,
    synced_to: datetime,
    synced_at: datetime,
) -> None:
    """Stamp the connection's busy-sync coverage window -- what refresh_busy_cache records (RF-12).

    read_busy judges freshness by this window, not by a per-row fetched_at, so a query outside the
    synced window can never read as covered (the F1-07 double-booking bug this fix closes).
    """
    connection.busy_synced_from = synced_from
    connection.busy_synced_to = synced_to
    connection.busy_synced_at = synced_at
    await session.flush()


def _window(now: datetime) -> TimeInterval:
    return TimeInterval(start=now, end=now + timedelta(days=7))


def _query(now: datetime, *, ttl: timedelta = timedelta(minutes=5)) -> BusyQuery:
    return BusyQuery(window=_window(now), now=now, ttl=ttl)


# --------------------------------------------------------------------------------------
# 1. Connection storage -- encrypted at rest, round-trips through load_credentials.
# --------------------------------------------------------------------------------------


async def test_store_google_connection_encrypts_token_at_rest(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    token_json = '{"token": "secret-access", "refresh_token": "secret-refresh"}'
    connection = await _connect(sqlite_session, tenant, fernet=fernet, token_json=token_json)

    # Stored bytes are ciphertext -- the plaintext token never appears at rest.
    assert connection.encrypted_credentials != token_json.encode("utf-8")
    assert b"secret-refresh" not in connection.encrypted_credentials
    assert connection.provider == "google"

    # And it round-trips back to the exact plaintext through load_credentials (decrypt).
    assert load_credentials(connection, fernet=fernet) == token_json


async def test_load_credentials_uses_the_app_secret_key(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    wrong = Fernet(derive_fernet_key("a-different-secret"))
    with pytest.raises(InvalidToken):
        load_credentials(connection, fernet=wrong)


# --------------------------------------------------------------------------------------
# 2. refresh_busy_cache -- writes the right rows, stamps fetched_at, replaces prior rows.
# --------------------------------------------------------------------------------------


async def test_refresh_busy_cache_writes_rows_for_the_window(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(
        freebusy_response=_freebusy(
            [
                ("2026-07-10T18:00:00Z", "2026-07-10T18:30:00Z"),
                ("2026-07-11T09:00:00Z", "2026-07-11T10:00:00Z"),
            ]
        )
    )

    written = await refresh_busy_cache(
        sqlite_session, connection=connection, window=_window(now), now=now, service=service
    )

    assert len(written) == 2
    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert len(rows) == 2
    for row in rows:
        assert _as_utc(row.fetched_at) == now

    # RF-12: the connection records the window it synced and when, so read_busy can judge coverage.
    assert _as_utc(connection.busy_synced_from) == now
    assert _as_utc(connection.busy_synced_to) == now + timedelta(days=7)
    assert _as_utc(connection.busy_synced_at) == now


async def test_refresh_busy_cache_stamps_coverage_when_calendar_is_empty(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(
        freebusy_response=_freebusy([])
    )  # an empty (but reachable) calendar

    written = await refresh_busy_cache(
        sqlite_session, connection=connection, window=_window(now), now=now, service=service
    )

    # Zero busy blocks, yet the window is recorded as synced -> an empty calendar is now
    # representable as "covered and fresh", not indistinguishable from "never synced" (RF-12/13).
    assert written == []
    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert len(rows) == 0
    assert _as_utc(connection.busy_synced_from) == now
    assert _as_utc(connection.busy_synced_to) == now + timedelta(days=7)
    assert _as_utc(connection.busy_synced_at) == now


async def test_refresh_busy_cache_replaces_previous_rows(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    old = now - timedelta(hours=3)
    await _seed_busy(
        sqlite_session,
        connection,
        fetched_at=old,
        blocks=[(now + timedelta(hours=1), now + timedelta(hours=2))],
    )

    service = FakeGoogleService(
        freebusy_response=_freebusy([("2026-07-12T08:00:00Z", "2026-07-12T09:00:00Z")])
    )
    await refresh_busy_cache(
        sqlite_session, connection=connection, window=_window(now), now=now, service=service
    )

    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert len(rows) == 1  # the stale row is gone, only the freshly-fetched block remains
    assert _as_utc(rows[0].fetched_at) == now


# --------------------------------------------------------------------------------------
# 3. read_busy -- TTL + RF-13 safe degradation.
# --------------------------------------------------------------------------------------


def _boom_factory(_: ExternalConnection) -> Any:
    raise AssertionError("Google must NOT be contacted when the cache covers the query window")


def _failing_factory(_: ExternalConnection) -> Any:
    return FakeGoogleService(freebusy_error=RuntimeError("google unreachable"))


class _RecordingFactory:
    """A service_factory that records whether it was invoked and returns a canned fake service."""

    def __init__(self, service: FakeGoogleService) -> None:
        self._service = service
        self.calls = 0

    def __call__(self, _: ExternalConnection) -> Any:
        self.calls += 1
        return self._service


async def test_read_busy_inside_covered_fresh_window_returns_cache_without_calling_google(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    busy_start, busy_end = now + timedelta(hours=1), now + timedelta(hours=2)
    await _seed_busy(sqlite_session, connection, fetched_at=now, blocks=[(busy_start, busy_end)])
    # The synced window fully contains the query window and was stamped just now -> covered + fresh.
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now,
        synced_to=now + timedelta(days=7),
        synced_at=now,
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_boom_factory,  # would raise if read_busy tried to refresh
    )

    assert result.status is BusyStatus.FRESH
    assert result.is_available
    assert not result.is_degraded
    assert result.busy == (TimeInterval(start=busy_start, end=busy_end),)


async def test_read_busy_outside_covered_window_refreshes_even_when_time_fresh(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    # The cache was synced JUST NOW (time-fresh) but for LAST week's window. A query for the coming
    # week is NOT covered by it, so the stale-window cache must never be reused -- doing so is the
    # F1-07 double-booking bug. read_busy must contact Google to refresh for the queried window.
    await _seed_busy(
        sqlite_session,
        connection,
        fetched_at=now,
        blocks=[(now - timedelta(days=8), now - timedelta(days=8) + timedelta(hours=1))],
    )
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now - timedelta(days=14),
        synced_to=now - timedelta(days=7),
        synced_at=now,  # recently fetched -- but for the wrong window
    )
    fresh = FakeGoogleService(
        freebusy_response=_freebusy([("2026-07-13T15:00:00Z", "2026-07-13T15:45:00Z")])
    )
    factory = _RecordingFactory(fresh)

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=factory,
    )

    assert factory.calls == 1  # the stale-window cache did NOT satisfy a next-week query
    assert result.status is BusyStatus.FRESH
    assert result.busy == (
        TimeInterval(
            start=datetime(2026, 7, 13, 15, 0, tzinfo=UTC),
            end=datetime(2026, 7, 13, 15, 45, tzinfo=UTC),
        ),
    )


async def test_read_busy_time_stale_then_refresh_success_returns_fresh(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    # Covered window, but the sync is older than the TTL -> time-stale, so a refresh is attempted.
    await _seed_busy(
        sqlite_session,
        connection,
        fetched_at=now - timedelta(hours=1),
        blocks=[(now + timedelta(hours=1), now + timedelta(hours=2))],
    )
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now,
        synced_to=now + timedelta(days=7),
        synced_at=now - timedelta(hours=1),
    )
    fresh = FakeGoogleService(
        freebusy_response=_freebusy([("2026-07-13T15:00:00Z", "2026-07-13T15:45:00Z")])
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=lambda _: fresh,
    )

    assert result.status is BusyStatus.FRESH
    assert result.is_available
    assert result.busy == (
        TimeInterval(
            start=datetime(2026, 7, 13, 15, 0, tzinfo=UTC),
            end=datetime(2026, 7, 13, 15, 45, tzinfo=UTC),
        ),
    )


async def test_read_busy_time_stale_refresh_failure_with_full_coverage_serves_last_known_degraded(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    last_start, last_end = now + timedelta(hours=1), now + timedelta(hours=2)
    # Prior coverage FULLY contains the query window -> the last-known copy is complete for it, so a
    # failed refresh may be served as STALE/degraded rather than refused.
    await _seed_busy(
        sqlite_session,
        connection,
        fetched_at=now - timedelta(hours=1),
        blocks=[(last_start, last_end)],
    )
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now,
        synced_to=now + timedelta(days=7),
        synced_at=now - timedelta(hours=1),
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_failing_factory,
    )

    assert result.status is BusyStatus.STALE
    assert result.is_available  # slots may still be offered from the last-known copy
    assert result.is_degraded
    assert result.busy == (TimeInterval(start=last_start, end=last_end),)


async def test_read_busy_refresh_failure_with_partial_coverage_is_unavailable(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    # Coverage spans only the first 3 days of the 7-day query window -> the cache is INCOMPLETE for
    # the query. A failed refresh must NOT serve partial data as if complete: refuse (UNAVAILABLE),
    # because the uncovered tail could hide a conflict and become a double-booking (RF-13).
    await _seed_busy(
        sqlite_session,
        connection,
        fetched_at=now,
        blocks=[(now + timedelta(hours=1), now + timedelta(hours=2))],
    )
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now,
        synced_to=now + timedelta(days=3),
        synced_at=now,
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_failing_factory,
    )

    assert result.status is BusyStatus.UNAVAILABLE
    assert not result.is_available
    assert result.busy == ()


async def test_read_busy_no_coverage_and_refresh_failure_is_unavailable(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_failing_factory,
    )

    # Never synced (no coverage) AND Google unreachable -> refuse slots (unknown is never free).
    assert result.status is BusyStatus.UNAVAILABLE
    assert not result.is_available
    assert result.busy == ()


async def test_read_busy_empty_but_covered_fresh_window_returns_fresh_without_refresh(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    # A completed sync of an EMPTY calendar: coverage stamped, zero cached busy rows. This must read
    # as covered + fresh (busy=()) WITHOUT a re-refresh -- empty is not "unknown".
    await _set_coverage(
        sqlite_session,
        connection,
        synced_from=now,
        synced_to=now + timedelta(days=7),
        synced_at=now,
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_boom_factory,  # would raise if read_busy tried to refresh
    )

    assert result.status is BusyStatus.FRESH
    assert result.is_available
    assert not result.is_degraded
    assert result.busy == ()


async def test_read_busy_no_connection_is_free_not_unavailable(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    user = await _first_user(sqlite_session, tenant)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=user.id,
        query=_query(now),
        service_factory=_boom_factory,
    )

    # A host with no connected external calendar has no external busy -- that is available + empty,
    # NOT unavailable (unavailable is reserved for a connection we cannot read).
    assert result.status is BusyStatus.FRESH
    assert result.is_available
    assert result.busy == ()


async def test_read_busy_is_tenant_isolated(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant_a = await tenant_factory(sqlite_session, slug="a", email="a@host.test")
    tenant_b = await tenant_factory(sqlite_session, slug="b", email="b@host.test")
    user_a = await _first_user(sqlite_session, tenant_a)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    # Tenant B has a connection with a fresh, covered busy sync; tenant A has none.
    conn_b = await _connect(sqlite_session, tenant_b, fernet=fernet, account_email="b@gmail.com")
    await _seed_busy(
        sqlite_session,
        conn_b,
        fetched_at=now,
        blocks=[(now + timedelta(hours=1), now + timedelta(hours=2))],
    )
    await _set_coverage(
        sqlite_session,
        conn_b,
        synced_from=now,
        synced_to=now + timedelta(days=7),
        synced_at=now,
    )

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant_a.id,
        host_user_id=user_a.id,
        query=_query(now),
        service_factory=_boom_factory,
    )

    # Tenant A must never see tenant B's busy blocks.
    assert result.status is BusyStatus.FRESH
    assert result.busy == ()


# --------------------------------------------------------------------------------------
# 4. Booking event lifecycle -- id + Meet URL, CalendarSyncError on Google failure.
# --------------------------------------------------------------------------------------


async def test_create_event_for_booking_returns_id_and_meeting_url(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(insert_result=_INSERT_RESULT)

    event_id, meeting_url = await create_event_for_booking(
        calendar_id=DEFAULT_CALENDAR_ID, request=_event(now), service=service
    )

    assert event_id == "evt-123"
    assert meeting_url == "https://meet.google.com/abc-defg-hij"


async def test_create_event_for_booking_google_failure_raises_calendar_sync_error(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(insert_error=RuntimeError("insert failed"))

    with pytest.raises(CalendarSyncError):
        await create_event_for_booking(
            calendar_id=DEFAULT_CALENDAR_ID, request=_event(now), service=service
        )


async def test_delete_event_for_booking_calls_google(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    service = FakeGoogleService()

    await delete_event_for_booking(
        calendar_id=DEFAULT_CALENDAR_ID, external_event_id="evt-123", service=service
    )

    assert service.events().deleted == ["evt-123"]


async def test_delete_event_for_booking_google_failure_raises_calendar_sync_error(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    service = FakeGoogleService(delete_error=RuntimeError("delete failed"))

    with pytest.raises(CalendarSyncError):
        await delete_event_for_booking(
            calendar_id=DEFAULT_CALENDAR_ID, external_event_id="evt-123", service=service
        )


async def test_reschedule_event_for_booking_replaces_the_event(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(insert_result=_INSERT_RESULT)

    event_id, meeting_url = await reschedule_event_for_booking(
        source_calendar_id=DEFAULT_CALENDAR_ID,
        source_service=service,
        target_calendar_id=DEFAULT_CALENDAR_ID,
        target_service=service,
        external_event_id="old-evt",
        request=_event(now),
    )

    assert event_id == "evt-123"
    assert meeting_url == "https://meet.google.com/abc-defg-hij"
    assert service.events().deleted == ["old-evt"]  # the previous event was removed


async def test_reschedule_event_for_booking_google_failure_raises_calendar_sync_error(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect(sqlite_session, tenant, fernet=fernet)
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = FakeGoogleService(insert_error=RuntimeError("insert failed"))

    with pytest.raises(CalendarSyncError):
        await reschedule_event_for_booking(
            source_calendar_id=DEFAULT_CALENDAR_ID,
            source_service=service,
            target_calendar_id=DEFAULT_CALENDAR_ID,
            target_service=service,
            external_event_id="old-evt",
            request=_event(now),
        )
