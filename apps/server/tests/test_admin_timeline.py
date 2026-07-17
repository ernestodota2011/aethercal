"""The admin's resource timeline (RF-28): hosts as rows, and why dragging BETWEEN them is refused.

The resource IS the host. ``event_types.host_id`` says whose event type a booking belongs to, and
that is what puts its chip on a row.

==Dragging a booking from one row to another has no backend operation — and that is not an oversight
to work around, it is a semantics nobody has designed.== ``bookings`` carries no ``host_id``: the
host lives on the EVENT TYPE. So "move this booking to Bruno" means "give this booking a DIFFERENT
event type" — another duration, another slug, another public page. Inventing an answer
here (silently re-point the event type? keep the duration and break it?) is exactly the kind of
guess this project keeps paying for. So the gesture is REFUSED, out loud, with its reason — and the
ordinary same-row drag keeps working untouched.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.runtime import AdminRuntime, configure_runtime
from aethercal.server.admin.state import AdminState
from aethercal.server.db import Base
from aethercal.server.db.models import Booking, Tenant
from aethercal.server.passwords import hash_password
from aethercal.server.services.bookings import BookingParams, create_booking
from aethercal.server.services.rbac import PrincipalKind

Sessionmaker = async_sessionmaker[AsyncSession]


def _future_weekday(hour: int, *, days_ahead: int = 7) -> datetime:
    """A future Mon-Fri instant at ``hour``:00 UTC (well inside the 30-day max-advance window).

    The same pattern the rest of the admin calendar suite uses: the RESCHEDULE path reads the real
    clock, so a slot fixed in the past would be refused for being off-offer and mask what these
    tests are actually about.
    """
    base = datetime.now(UTC) + timedelta(days=days_ahead)
    while base.weekday() >= 5:  # roll Sat/Sun forward to Monday
        base += timedelta(days=1)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def _naive_iso(moment: datetime) -> str:
    """The naive-UTC wall-time ISO string the calendar renders and echoes back."""
    return moment.astimezone(UTC).replace(tzinfo=None, microsecond=0).isoformat()


_NOW = datetime.now(UTC)
_START = _future_weekday(9)  # inside the 09:00-17:00 weekly pattern
_MOVED = _future_weekday(11)  # same weekday, still inside it

_WEEKLY_FORM = {
    "name": "Weekly",
    "timezone": "UTC",
    "weekdays": "0,1,2,3,4",
    "start": "09:00",
    "end": "17:00",
}


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[Sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        session.add(Tenant(slug="acme", name="Acme"))
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _authed(maker: Sessionmaker) -> AdminState:
    configure_runtime(
        AdminRuntime(
            sessionmaker=maker,
            config=AdminConfig(
                username="op", password_hash=hash_password("s3cret"), tenant_slug=None
            ),
        )
    )
    state = AdminState(_reflex_internal_init=True)
    state._authenticated = True
    state._principal_kind = PrincipalKind.BOOTSTRAP_OPERATOR.value
    return state


async def _two_hosts(state: AdminState) -> tuple[str, str]:
    """Ana and Bruno, each with their own event type on the business's weekly pattern."""
    await AdminState.create_host.fn(
        state, {"name": "Ana", "email": "ana@example.com", "timezone": "UTC"}
    )
    await AdminState.create_host.fn(
        state, {"name": "Bruno", "email": "bruno@example.com", "timezone": "UTC"}
    )
    await AdminState.create_schedule.fn(state, _WEEKLY_FORM)
    await AdminState.load_hosts.fn(state)
    assert state.error == ""
    # By NAME, never by index: hosts are ordered by ``(created_at, id)``, and SQLite stamps both of
    # these in the same tick — so the tiebreak is a random UUID and the positions are not ours to
    # assume. (On Postgres the timestamps differ and creation order holds.)
    by_name = {row["name"]: row["id"] for row in state.hosts}
    ana, bruno = by_name["Ana"], by_name["Bruno"]

    for host_id, slug in ((ana, "ana-intro"), (bruno, "bruno-intro")):
        await AdminState.create_event_type.fn(
            state,
            {
                "host_id": host_id,
                "slug": slug,
                "title": slug,
                "schedule": "Weekly",
                "duration_min": "30",
                "max_advance_days": "30",
            },
        )
        assert state.error == ""
    return ana, bruno


async def _book_with(state: AdminState, maker: Sessionmaker, *, slug: str) -> uuid.UUID:
    """A confirmed booking on the event type identified by ``slug``."""
    await AdminState.load_event_types.fn(state)
    event_type_id = next(uuid.UUID(row["id"]) for row in state.event_types if row["slug"] == slug)
    async with maker() as session, session.begin():
        tenant = (await session.scalars(select(Tenant))).one()
        booking = await create_booking(
            session,
            tenant_id=tenant.id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=_START,
                guest_name="Guest",
                guest_email="guest@example.com",
                guest_timezone="UTC",
            ),
            now=_NOW,
        )
        await session.flush()
        return booking.id


async def _drain(agen: AsyncIterator[object]) -> None:
    async for _ in agen:
        pass


async def _confirmed(maker: Sessionmaker) -> Booking:
    async with maker() as session:
        return (
            await session.scalars(
                select(Booking).where(Booking.status == BookingStatus.CONFIRMED.value)
            )
        ).one()


# --------------------------------------------------------------------------------------
# The timeline is a view the operator can actually reach.
# --------------------------------------------------------------------------------------


async def test_the_timeline_is_a_view_the_switcher_accepts(maker: Sessionmaker) -> None:
    """An unlisted view is IGNORED by the switcher — silently. A timeline the component renders and
    the state refuses to select would ship, and do nothing, and fail no test."""
    state = await _authed(maker)

    AdminState.set_calendar_view.fn(state, "timeline")

    assert state.calendar_view == "timeline"


async def test_switching_to_the_timeline_loads_its_period(maker: Sessionmaker) -> None:
    state = await _authed(maker)
    await _two_hosts(state)

    await AdminState.on_calendar_view_change.fn(
        state,
        {
            "view": "timeline",
            "from": _naive_iso(_START),
            "to": _naive_iso(_START + timedelta(days=7)),
        },
    )

    assert state.calendar_view == "timeline"
    assert state.calendar_anchor == _naive_iso(_START)
    assert state.error == ""


# --------------------------------------------------------------------------------------
# The rows are the hosts, and every chip knows which row it is on.
# --------------------------------------------------------------------------------------


async def test_the_timeline_rows_are_the_hosts(maker: Sessionmaker) -> None:
    state = await _authed(maker)
    ana, bruno = await _two_hosts(state)

    await AdminState.load_bookings.fn(state)

    assert {row["id"] for row in state.calendar_resources} == {ana, bruno}
    assert {row["title"] for row in state.calendar_resources} == {"Ana", "Bruno"}
    # The rows ARE the hosts: one row each, and the id is the host's (which is what a chip's
    # ``resourceId`` is matched against).
    assert {row["id"]: row["title"] for row in state.calendar_resources} == {
        ana: "Ana",
        bruno: "Bruno",
    }


async def test_a_booking_lands_on_the_row_of_its_event_types_host(maker: Sessionmaker) -> None:
    """The resource IS the host, and the host lives on the EVENT TYPE — the very fact that makes
    dragging between rows undefined."""
    state = await _authed(maker)
    _ana, bruno = await _two_hosts(state)
    booking_id = await _book_with(state, maker, slug="bruno-intro")

    await AdminState.load_bookings.fn(state)

    event = next(e for e in state.calendar_events if e["id"] == str(booking_id))
    assert event["resourceId"] == bruno


# --------------------------------------------------------------------------------------
# Dragging BETWEEN rows: refused, loudly, with the reason.
# --------------------------------------------------------------------------------------


async def test_dragging_a_booking_to_another_hosts_row_is_refused(maker: Sessionmaker) -> None:
    """==The gesture the component offers and the backend has no operation for.==

    Refused with the reason — and, the part that actually matters, the booking is UNCHANGED in the
    database. A refusal that still moved the appointment would be the silent no-op wearing an error
    message.
    """
    state = await _authed(maker)
    ana, _bruno = await _two_hosts(state)
    booking_id = await _book_with(state, maker, slug="bruno-intro")
    await AdminState.load_bookings.fn(state)

    await _drain(
        AdminState.on_calendar_event_drop.fn(
            state,
            {
                "id": str(booking_id),
                "start": _naive_iso(_MOVED),
                "end": _naive_iso(_MOVED + timedelta(minutes=30)),
                "resourceId": ana,  # dropped on ANA's row; the booking is BRUNO's
            },
        )
    )

    assert "anfitrión" in state.error

    row = await _confirmed(maker)
    assert row.id == booking_id
    assert row.start_at.replace(tzinfo=UTC) == _START  # it did NOT move

    # And the chip is back where it belongs: no optimistic move was left applied.
    event = next(e for e in state.calendar_events if e["id"] == str(booking_id))
    assert event["start"] == _naive_iso(_START)


async def test_dragging_within_the_same_row_still_reschedules(maker: Sessionmaker) -> None:
    """The refusal must not break the ordinary drag: within one row it is just a move in time."""
    state = await _authed(maker)
    _ana, bruno = await _two_hosts(state)
    await _book_with(state, maker, slug="bruno-intro")
    await AdminState.load_bookings.fn(state)
    booking_id = (await _confirmed(maker)).id

    await _drain(
        AdminState.on_calendar_event_drop.fn(
            state,
            {
                "id": str(booking_id),
                "start": _naive_iso(_MOVED),
                "end": _naive_iso(_MOVED + timedelta(minutes=30)),
                "resourceId": bruno,  # the SAME row it is already on
            },
        )
    )

    assert state.error == ""
    assert (await _confirmed(maker)).start_at.replace(tzinfo=UTC) == _MOVED


async def test_a_drag_from_a_view_with_no_rows_still_reschedules(maker: Sessionmaker) -> None:
    """Month / week / day carry no ``resourceId`` at all, so their drags must not be caught by the
    timeline's guard: an ABSENT resource is not a DIFFERENT resource."""
    state = await _authed(maker)
    await _two_hosts(state)
    await _book_with(state, maker, slug="bruno-intro")
    await AdminState.load_bookings.fn(state)
    booking_id = (await _confirmed(maker)).id

    await _drain(
        AdminState.on_calendar_event_drop.fn(
            state,
            {
                "id": str(booking_id),
                "start": _naive_iso(_MOVED),
                "end": _naive_iso(_MOVED + timedelta(minutes=30)),
            },  # no resourceId — the month view has no resource dimension
        )
    )

    assert state.error == ""
    assert (await _confirmed(maker)).start_at.replace(tzinfo=UTC) == _MOVED
