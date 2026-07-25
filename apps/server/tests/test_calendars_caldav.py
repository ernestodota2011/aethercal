"""The CalDAV busy provider unioned into the read path (C-03, RF-12/13/30, RNF-9).

Every test runs offline against the in-memory ``sqlite_session`` with a FAKE CalDAV client, exactly
as the Google service tests do. What is proven here, and never the apparent state:

* a ``caldav`` connection stores its ``{server_url, username, password}`` ENCRYPTED at rest and
  round-trips through ``load_credentials``; the calendar collection URL lives in an
  ``ExternalCalendarLink`` (C-03.3);
* ``refresh_busy_cache`` dispatches to the CalDAV freebusy query BY PROVIDER and caches its blocks;
* ``read_busy`` UNIONS a CalDAV connection's busy with a Google one -- breaking the Google
  monoculture (RNF-9) without changing Google's behaviour (C-03.1);
* the RF-13 degradation is identical: a CalDAV connection we cannot read, with no covering cache,
  makes the WHOLE host ``UNAVAILABLE`` (fail-closed), and a successful refresh reads ``FRESH``,
  unioned with Google (C-03.4);
* CalDAV is READ-ONLY: it is never a booking write target (``resolve_calendar_target`` ignores it).
"""

from __future__ import annotations

import json
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
from aethercal.server.services.calendars import (
    CALDAV_PROVIDER,
    BusyQuery,
    BusyStatus,
    CaldavCredential,
    GoogleCredential,
    busy_calendar_ids,
    load_active_connections,
    load_credentials,
    read_busy,
    refresh_busy_cache,
    resolve_calendar_target,
    store_caldav_connection,
    store_google_connection,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
CAL_URL = "https://cloud.example/remote.php/dav/calendars/host/personal/"


# --------------------------------------------------------------------------------------
# A fake CalDAV client -- returns a canned VFREEBUSY down the same ``report`` call the live
# ``integrations/caldav/client.query_busy`` makes, or raises to model an unreachable server.
# --------------------------------------------------------------------------------------


def _vfreebusy(blocks: list[tuple[str, str]]) -> str:
    lines = "".join(f"FREEBUSY;FBTYPE=BUSY:{start}/{end}\r\n" for start, end in blocks)
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Nextcloud//EN\r\n"
        "BEGIN:VFREEBUSY\r\n"
        "DTSTART:20260710T000000Z\r\n"
        "DTEND:20260717T000000Z\r\n"
        f"{lines}"
        "END:VFREEBUSY\r\n"
        "END:VCALENDAR\r\n"
    )


class FakeCaldavClient:
    """Returns a canned VFREEBUSY (or raises, to model an unreachable CalDAV server)."""

    def __init__(
        self, *, blocks: list[tuple[str, str]] | None = None, error: Exception | None = None
    ) -> None:
        self._document = None if blocks is None else _vfreebusy(blocks)
        self._error = error
        self.reports: list[str] = []

    def report(self, calendar_url: str, *, body: str) -> str:
        if self._error is not None:
            raise self._error
        self.reports.append(calendar_url)
        assert self._document is not None
        return self._document


def _fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


async def _host(session: AsyncSession, tenant: Tenant) -> User:
    return (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()


async def _connect_caldav(  # noqa: PLR0913 - a test helper mirroring the CaldavCredential fields
    session: AsyncSession,
    tenant: Tenant,
    *,
    fernet: Fernet,
    username: str = "host",
    server_url: str = "https://cloud.example",
    calendar_url: str = CAL_URL,
) -> ExternalConnection:
    connection = await store_caldav_connection(
        session,
        tenant_id=tenant.id,
        user_id=(await _host(session, tenant)).id,
        credential=CaldavCredential(
            server_url=server_url,
            username=username,
            password="app-password",
            calendar_url=calendar_url,
        ),
        fernet=fernet,
    )
    await session.flush()
    return connection


async def _connect_google(
    session: AsyncSession, tenant: Tenant, *, fernet: Fernet, account_email: str = "host@gmail.com"
) -> ExternalConnection:
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=(await _host(session, tenant)).id,
        credential=GoogleCredential(account_email=account_email, token_json='{"token": "at"}'),
        fernet=fernet,
    )
    await session.flush()
    return connection


async def _seed_cover(
    session: AsyncSession,
    connection: ExternalConnection,
    *,
    blocks: list[tuple[datetime, datetime]],
) -> None:
    for start, end in blocks:
        session.add(
            BusyCache(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                start_at=start,
                end_at=end,
                fetched_at=NOW,
            )
        )
    connection.busy_synced_from = NOW
    connection.busy_synced_to = NOW + timedelta(days=7)
    connection.busy_synced_at = NOW
    await session.flush()


def _query(*, ttl: timedelta = timedelta(minutes=5)) -> BusyQuery:
    return BusyQuery(window=TimeInterval(start=NOW, end=NOW + timedelta(days=7)), now=NOW, ttl=ttl)


# --------------------------------------------------------------------------------------
# C-03.3 -- storage: encrypted server/username/password + the calendar URL as a busy link.
# --------------------------------------------------------------------------------------


async def test_store_caldav_connection_encrypts_credentials_and_links_the_calendar(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    fernet = _fernet()

    connection = await _connect_caldav(sqlite_session, tenant, fernet=fernet)

    assert connection.provider == CALDAV_PROVIDER
    # The account identity folds in the server origin so the same username on two servers stays two
    # connections.
    assert connection.account_email == "host@cloud.example"
    # The app-password never appears at rest -- it is Fernet ciphertext.
    assert b"app-password" not in connection.encrypted_credentials
    # And it round-trips back to the exact JSON payload through load_credentials (decrypt).
    payload = json.loads(load_credentials(connection, fernet=fernet))
    assert payload == {
        "server_url": "https://cloud.example",
        "username": "host",
        "password": "app-password",
    }

    # The calendar collection URL is stored on the link (C-03.3), flagged busy but NOT a write
    # target -- CalDAV is read-only.
    link = (
        await sqlite_session.scalars(
            select(ExternalCalendarLink).where(ExternalCalendarLink.connection_id == connection.id)
        )
    ).one()
    assert link.external_calendar_id == CAL_URL
    assert link.busy is True
    assert link.is_booking_target is False
    assert await busy_calendar_ids(sqlite_session, connection=connection) == [CAL_URL]


async def test_re_storing_the_same_caldav_calendar_is_idempotent(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    fernet = _fernet()

    for _ in range(2):
        connection = await _connect_caldav(sqlite_session, tenant, fernet=fernet)

    links = (
        await sqlite_session.scalars(
            select(ExternalCalendarLink).where(ExternalCalendarLink.connection_id == connection.id)
        )
    ).all()
    assert len(links) == 1  # a password rotation / re-run must not pile up link rows


async def test_re_storing_caldav_with_a_different_calendar_replaces_the_old_one(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    fernet = _fernet()
    old_url = "https://cloud.example/remote.php/dav/calendars/host/old/"
    new_url = "https://cloud.example/remote.php/dav/calendars/host/new/"
    connection = await _connect_caldav(sqlite_session, tenant, fernet=fernet, calendar_url=old_url)
    # Warm the cache so invalidation-on-real-change is observable.
    await _seed_cover(
        sqlite_session, connection, blocks=[(NOW + timedelta(hours=1), NOW + timedelta(hours=2))]
    )
    assert await busy_calendar_ids(sqlite_session, connection=connection) == [old_url]

    # Re-point the connection at a DIFFERENT calendar on the same server.
    await _connect_caldav(sqlite_session, tenant, fernet=fernet, calendar_url=new_url)

    # Only the NEW calendar is read now -- the stale one is retired, so a calendar the host meant to
    # stop using can never keep blocking slots, nor wedge the host at UNAVAILABLE if it later 404s.
    assert await busy_calendar_ids(sqlite_session, connection=connection) == [new_url]
    # The read set changed, so the availability cache was invalidated in the same transaction.
    assert connection.busy_synced_at is None
    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert rows == []


async def test_same_username_on_two_servers_stays_two_connections_and_both_union(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    fernet = _fernet()
    # The SAME username ("admin") on TWO different CalDAV servers.
    first = await _connect_caldav(
        sqlite_session,
        tenant,
        fernet=fernet,
        username="admin",
        server_url="https://cloud-a.example",
        calendar_url="https://cloud-a.example/dav/admin/",
    )
    second = await _connect_caldav(
        sqlite_session,
        tenant,
        fernet=fernet,
        username="admin",
        server_url="https://cloud-b.example",
        calendar_url="https://cloud-b.example/dav/admin/",
    )

    # NOT collapsed into one row: keying on username alone would have let the second overwrite the
    # first and silently drop its busy set (a missing busy set is a double-booking, RF-30).
    assert first.id != second.id
    persisted = (
        await sqlite_session.scalars(
            select(ExternalConnection).where(ExternalConnection.provider == CALDAV_PROVIDER)
        )
    ).all()
    assert {c.account_email for c in persisted} == {
        "admin@cloud-a.example",
        "admin@cloud-b.example",
    }

    # And BOTH participate in the host's busy union.
    a_busy = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    b_busy = (NOW + timedelta(hours=5), NOW + timedelta(hours=6))
    await _seed_cover(sqlite_session, first, blocks=[a_busy])
    await _seed_cover(sqlite_session, second, blocks=[b_busy])

    result = await read_busy(
        sqlite_session, tenant_id=tenant.id, host_user_id=host.id, query=_query()
    )
    assert result.status is BusyStatus.FRESH
    assert set(result.busy) == {
        TimeInterval(start=a_busy[0], end=a_busy[1]),
        TimeInterval(start=b_busy[0], end=b_busy[1]),
    }


async def test_store_caldav_refuses_a_cleartext_or_foreign_endpoint(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    fernet = _fernet()

    # http server -> HTTP Basic would put the app-password on the wire in the clear.
    with pytest.raises(ValueError, match="https"):
        await store_caldav_connection(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=host.id,
            credential=CaldavCredential(
                server_url="http://cloud.example",
                username="host",
                password="pw",
                calendar_url="http://cloud.example/cal/",
            ),
            fernet=fernet,
        )

    # A calendar on ANOTHER host -> the host's credentials must never be sent there.
    with pytest.raises(ValueError, match="origin"):
        await store_caldav_connection(
            sqlite_session,
            tenant_id=tenant.id,
            user_id=host.id,
            credential=CaldavCredential(
                server_url="https://cloud.example",
                username="host",
                password="pw",
                calendar_url="https://evil.example/cal/",
            ),
            fernet=fernet,
        )

    # The refusal is at the door: nothing was persisted for either attempt.
    persisted = (
        await sqlite_session.scalars(
            select(ExternalConnection).where(ExternalConnection.provider == CALDAV_PROVIDER)
        )
    ).all()
    assert persisted == []


# --------------------------------------------------------------------------------------
# refresh_busy_cache -- provider dispatch: a caldav connection uses the CalDAV freebusy query.
# --------------------------------------------------------------------------------------


async def test_refresh_busy_cache_uses_the_caldav_query_for_a_caldav_connection(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    connection = await _connect_caldav(sqlite_session, tenant, fernet=_fernet())
    client = FakeCaldavClient(
        blocks=[("20260711T090000Z", "20260711T100000Z"), ("20260712T140000Z", "20260712T150000Z")]
    )

    written = await refresh_busy_cache(
        sqlite_session, connection=connection, window=_query().window, now=NOW, service=client
    )

    assert client.reports == [CAL_URL]  # it reported against the linked CalDAV calendar URL
    assert len(written) == 2
    rows = (
        await sqlite_session.scalars(
            select(BusyCache).where(BusyCache.connection_id == connection.id)
        )
    ).all()
    assert len(rows) == 2


# --------------------------------------------------------------------------------------
# C-03.1 -- read_busy UNIONS a caldav connection's busy with a google one.
# --------------------------------------------------------------------------------------


async def test_read_busy_unions_caldav_and_google(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    fernet = _fernet()
    google = await _connect_google(sqlite_session, tenant, fernet=fernet)
    caldav = await _connect_caldav(sqlite_session, tenant, fernet=fernet)
    g_busy = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    c_busy = (NOW + timedelta(hours=5), NOW + timedelta(hours=6))
    await _seed_cover(sqlite_session, google, blocks=[g_busy])
    await _seed_cover(sqlite_session, caldav, blocks=[c_busy])

    def _boom(_: ExternalConnection) -> Any:  # both caches cover + are fresh -> no refresh
        raise AssertionError("must not refresh: both connections are covered + fresh")

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=host.id,
        query=_query(),
        service_factory=_boom,
    )

    assert result.status is BusyStatus.FRESH
    # The CalDAV busy is NOT dropped -- it is unioned with Google's (RF-30 across providers, RNF-9).
    assert set(result.busy) == {
        TimeInterval(start=g_busy[0], end=g_busy[1]),
        TimeInterval(start=c_busy[0], end=c_busy[1]),
    }


async def test_read_busy_refreshes_caldav_and_unions_it_with_a_cached_google(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    fernet = _fernet()
    google = await _connect_google(sqlite_session, tenant, fernet=fernet)
    caldav = await _connect_caldav(sqlite_session, tenant, fernet=fernet)
    g_busy = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    await _seed_cover(sqlite_session, google, blocks=[g_busy])  # google served from cache
    # caldav never synced -> read_busy must REFRESH it through the factory.
    caldav_client = FakeCaldavClient(blocks=[("20260713T150000Z", "20260713T154500Z")])

    def factory(connection: ExternalConnection) -> Any:
        assert connection.id == caldav.id  # only the uncovered caldav connection is refreshed
        return caldav_client

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=host.id,
        query=_query(),
        service_factory=factory,
    )

    assert result.status is BusyStatus.FRESH
    assert set(result.busy) == {
        TimeInterval(start=g_busy[0], end=g_busy[1]),
        TimeInterval(
            start=datetime(2026, 7, 13, 15, 0, tzinfo=UTC),
            end=datetime(2026, 7, 13, 15, 45, tzinfo=UTC),
        ),
    }


# --------------------------------------------------------------------------------------
# C-03.4 -- RF-13 degradation is identical: unreadable caldav + no coverage -> UNAVAILABLE.
# --------------------------------------------------------------------------------------


async def test_unreachable_caldav_without_coverage_makes_the_host_unavailable(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    fernet = _fernet()
    google = await _connect_google(sqlite_session, tenant, fernet=fernet)
    caldav = await _connect_caldav(sqlite_session, tenant, fernet=fernet)
    await _seed_cover(sqlite_session, google, blocks=[])  # healthy, covered + fresh, empty busy
    # caldav never synced AND unreachable -> there IS an external busy we cannot read.

    def factory(connection: ExternalConnection) -> Any:
        if connection.id == caldav.id:
            return FakeCaldavClient(error=RuntimeError("connection refused"))
        raise AssertionError("google is covered + fresh; it must not be refreshed")

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=host.id,
        query=_query(),
        service_factory=factory,
    )

    # Fail-closed, identical to Google: one calendar we cannot establish refuses the whole host.
    assert result.status is BusyStatus.UNAVAILABLE
    assert not result.is_available
    assert result.busy == ()


async def test_caldav_refresh_failure_with_full_coverage_serves_last_known_degraded(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    connection = await _connect_caldav(sqlite_session, tenant, fernet=_fernet())
    last = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    # Covered window but time-stale -> a refresh is attempted; it fails, and the prior coverage
    # fully contains the query window, so the last-known copy is served STALE (RF-13), like Google.
    await _seed_cover(sqlite_session, connection, blocks=[last])
    connection.busy_synced_at = NOW - timedelta(hours=1)
    await sqlite_session.flush()

    def factory(_: ExternalConnection) -> Any:
        return FakeCaldavClient(error=RuntimeError("caldav 503"))

    result = await read_busy(
        sqlite_session,
        tenant_id=tenant.id,
        host_user_id=host.id,
        query=_query(),
        service_factory=factory,
    )

    assert result.status is BusyStatus.STALE
    assert result.is_available and result.is_degraded
    assert result.busy == (TimeInterval(start=last[0], end=last[1]),)


# --------------------------------------------------------------------------------------
# CalDAV is READ-ONLY -- never a booking write target, and excluded from the write path.
# --------------------------------------------------------------------------------------


async def test_caldav_is_never_a_booking_target(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _host(sqlite_session, tenant)
    await _connect_caldav(sqlite_session, tenant, fernet=_fernet())

    # A host whose ONLY connection is CalDAV has no calendar to WRITE bookings into: CalDAV feeds
    # busy, it does not receive events. resolve_calendar_target (google-only write path) sees none.
    assert (
        await resolve_calendar_target(sqlite_session, tenant_id=tenant.id, user_id=host.id) is None
    )
    # And the write path's connection loader (google-default) does not surface the caldav row.
    assert await load_active_connections(sqlite_session, tenant_id=tenant.id, user_id=host.id) == []
