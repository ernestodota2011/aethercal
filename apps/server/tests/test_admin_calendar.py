"""Tests for the admin's calendar surface (F2-F): the agenda adopts ``aethercal.ui.Calendar``.

The admin's booking list is replaced by the calendar component. These tests drive the real Reflex
state handlers against the in-process service layer + an in-memory aiosqlite DB (the same seam the
websocket triggers in a browser), proving:

* a booking projects onto a ``CalendarEvent`` (naive-UTC wall-time, editable iff confirmed);
* ``load_bookings`` populates ``calendar_events`` with the tenant's active bookings;
* drag (``on_event_drop``) / resize (``on_event_resize``) reschedule with OPTIMISTIC reconciliation:
  the move shows immediately, then commits to the server's confirmed state or rolls back on refusal;
* range-select opens the create-booking affordance; event-click selects a booking to manage;
* the view switcher drives the ``view`` prop; every new handler is auth-guarded (RF-18).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.server.admin import runtime as runtime_mod
from aethercal.server.admin import service
from aethercal.server.admin import state as state_mod
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.format import booking_event
from aethercal.server.admin.passwords import hash_password
from aethercal.server.admin.ratelimit import LOGIN_LIMITER
from aethercal.server.admin.runtime import AdminRuntime, configure_runtime
from aethercal.server.admin.state import AdminState
from aethercal.server.db import Base
from aethercal.server.db.models import Booking, Tenant, User
from aethercal.server.services.bookings import BookingParams, create_booking

Sessionmaker = async_sessionmaker[AsyncSession]


def _admin(maker: Sessionmaker) -> AdminRuntime:
    """The session accessor the admin service layer takes, over the offline sessionmaker (B-01).

    The service functions no longer accept a raw ``async_sessionmaker``. Under RLS a session opened
    without a business bound reads ZERO rows — silently — so the factory is private to the runtime
    and the only way in is ``admin_session``, which resolves the business and BINDS it before it
    yields. The suite goes through the same door the panel does: a harness that kept the old
    shortcut would be exercising a seam nobody ships.
    """
    return AdminRuntime(
        sessionmaker=maker,
        config=AdminConfig(username="admin", password_hash="x", tenant_slug=None),
    )


_WEEKLY_FORM = {
    "name": "Weekly",
    "timezone": "UTC",
    "weekdays": "0,1,2,3,4",
    "start": "09:00",
    "end": "17:00",
}


def _future_weekday(hour: int, *, days_ahead: int = 7) -> datetime:
    """A future Mon-Fri instant at ``hour``:00 UTC (well inside the 30-day max-advance window)."""
    base = datetime.now(UTC) + timedelta(days=days_ahead)
    while base.weekday() >= 5:  # roll Sat/Sun forward to Monday
        base += timedelta(days=1)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def _naive_iso(moment: datetime) -> str:
    """The naive-UTC wall-time ISO string the calendar renders/echoes."""
    return moment.astimezone(UTC).replace(tzinfo=None, microsecond=0).isoformat()


SLOT_A = _future_weekday(9)
SLOT_B = _future_weekday(11)  # same weekday, still inside 09:00-17:00
OFF_HOURS = _future_weekday(3)  # outside the schedule → a refused reschedule


def _month_window(moment: datetime) -> dict[str, str]:
    """A calendar on_range_change payload for the month containing ``moment`` (F2-NAV).

    Mirrors the JS ``getVisibleRange("month", ...)`` output: ``from`` = the 1st at midnight, ``to``
    = the exclusive 1st of the next month, as naive-local ISO strings.
    """
    first = date(moment.year, moment.month, 1)
    nxt = date(moment.year + (moment.month == 12), (moment.month % 12) + 1, 1)
    return {
        "view": "month",
        "from": f"{first.isoformat()}T00:00:00",
        "to": f"{nxt.isoformat()}T00:00:00",
    }


@pytest.fixture(autouse=True)
def _clean_runtime() -> AsyncIterator[None]:
    """Reset the process-global runtime + login limiter around each test."""
    saved = runtime_mod._Holder.value
    runtime_mod._Holder.value = None
    LOGIN_LIMITER.reset()
    yield
    runtime_mod._Holder.value = saved
    LOGIN_LIMITER.reset()


@pytest_asyncio.fixture
async def seeded_maker() -> AsyncIterator[Sessionmaker]:
    """An in-memory sessionmaker with one tenant + host user."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        tenant = Tenant(slug="acme", name="Acme")
        session.add(tenant)
        await session.flush()
        session.add(User(tenant_id=tenant.id, email="h@example.com", name="Host", timezone="UTC"))
    try:
        yield maker
    finally:
        await engine.dispose()


def _state() -> AdminState:
    return AdminState(_reflex_internal_init=True)


async def _authed_state(maker: Sessionmaker) -> AdminState:
    config = AdminConfig(username="op", password_hash=hash_password("s3cret"), tenant_slug=None)
    configure_runtime(AdminRuntime(sessionmaker=maker, config=config))
    state = _state()
    state._authenticated = True
    return state


async def _tenant_id(maker: Sessionmaker) -> uuid.UUID:
    async with maker() as session:
        tenant = (await session.scalars(select(Tenant))).first()
        assert tenant is not None
        return tenant.id


async def _seeded_host_id(state: AdminState) -> str:
    """The tenant's ONE host, as a form value.

    RF-30 made the host an EXPLICIT field on the event-type form. It used to be injected by the
    service — the tenant's first user — which is precisely why a business's second host could never
    be given an event type. So every create now states which host it means, these tests included.
    """
    await AdminState.load_hosts.fn(state)
    assert len(state.hosts) == 1, "these tests seed a single-host tenant"
    return state.hosts[0]["id"]


async def _seed_event_type(state: AdminState) -> None:
    await AdminState.create_schedule.fn(state, _WEEKLY_FORM)
    await AdminState.create_event_type.fn(
        state,
        {
            "host_id": await _seeded_host_id(state),
            "slug": "intro",
            "title": "Introducción",
            "schedule": "Weekly",
            "duration_min": "30",
            "max_advance_days": "30",
        },
    )
    assert state.error == ""


async def _book_at(maker: Sessionmaker, *, event_type_id: uuid.UUID, start: datetime) -> uuid.UUID:
    tenant_id = await _tenant_id(maker)
    async with maker() as session, session.begin():
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=start,
                guest_name="Guest",
                guest_email="guest@example.com",
                guest_timezone="UTC",
            ),
            now=datetime.now(UTC),
        )
        await session.flush()
        return booking.id


async def _drain(agen: AsyncIterator[object]) -> None:
    async for _ in agen:
        pass


def _booking_read(**overrides: object) -> BookingRead:
    raw = SimpleNamespace(
        id=uuid.uuid4(),
        event_type_id=uuid.uuid4(),
        start_at=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 6, 9, 30, tzinfo=UTC),
        status=BookingStatus.CONFIRMED,
        guest_name="Ada Lovelace",
        guest_email="ada@example.com",
        guest_timezone="UTC",
        guest_notes=None,
        answers={},
        meeting_url=None,
        rescheduled_from_id=None,
        cancelled_at=None,
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )
    for key, value in overrides.items():
        setattr(raw, key, value)
    return BookingRead.model_validate(raw)


# --------------------------------------------------------------------------------------
# booking_event projection (pure).
# --------------------------------------------------------------------------------------


def test_booking_event_projects_a_confirmed_booking() -> None:
    read = _booking_read()
    event = booking_event(read)
    assert event["id"] == str(read.id)
    assert event["title"] == "Ada Lovelace"
    # Naive-UTC wall-time so the calendar's local-time parser places it at the right hour.
    assert event["start"] == "2026-07-06T09:00:00"
    assert event["end"] == "2026-07-06T09:30:00"
    assert event["editable"] is True


def test_booking_event_marks_a_non_confirmed_booking_non_editable() -> None:
    read = _booking_read(status=BookingStatus.PENDING)
    event = booking_event(read)
    assert event["editable"] is False


def test_booking_event_normalizes_an_aware_offset_to_utc_wall_time() -> None:
    plus_two = timezone(timedelta(hours=2))
    read = _booking_read(start_at=datetime(2026, 7, 6, 11, 0, tzinfo=plus_two))
    # 11:00+02:00 == 09:00 UTC → rendered as naive UTC wall-time.
    assert booking_event(read)["start"] == "2026-07-06T09:00:00"


# --------------------------------------------------------------------------------------
# Loading bookings into the calendar.
# --------------------------------------------------------------------------------------


async def test_load_bookings_populates_calendar_events(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)

    await AdminState.load_bookings.fn(state)

    assert state.error == ""
    assert [e["id"] for e in state.calendar_events] == [str(booking_id)]
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_A)


async def test_load_bookings_excludes_cancelled_bookings_from_the_calendar(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await service.cancel_booking_action(
        _admin(seeded_maker), tenant_slug=None, booking_id=booking_id
    )

    await AdminState.load_bookings.fn(state)
    assert state.calendar_events == []


# --------------------------------------------------------------------------------------
# Drag / resize → optimistic reschedule (the F2-F heart, RF-21).
# --------------------------------------------------------------------------------------


async def test_drag_applies_the_move_optimistically_before_the_server_answers(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    gen = AdminState.on_calendar_event_drop.fn(state, payload)
    await gen.__anext__()  # run up to the first yield: the optimistic apply
    # The chip has already moved to the dropped slot, before any DB round-trip.
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_B)
    await _drain(gen)


async def test_drag_commits_the_reschedule_and_persists(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))

    # Committed to the server's confirmed state: one active event, now at the new slot.
    assert state.error == ""
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_B)
    # Persisted: the reschedule opened a confirmed successor and cancelled the original.
    async with seeded_maker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CANCELLED


async def test_drag_to_a_refused_slot_rolls_back(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    payload = {"id": str(booking_id), "start": _naive_iso(OFF_HOURS), "end": _naive_iso(OFF_HOURS)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))

    # Rolled back to the authoritative slot, with an operator-facing error.
    assert state.error != ""
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_A)
    async with seeded_maker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CONFIRMED  # untouched by the refused move


async def test_resize_that_moves_the_start_reschedules_like_a_drag(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_resize.fn(state, payload))
    assert state.error == ""
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_B)


async def test_resize_that_only_changes_duration_is_rejected(seeded_maker: Sessionmaker) -> None:
    # A bottom-edge resize keeps the START and extends the END — a pure duration change. A booking's
    # duration is event-type-derived, so this cannot persist: it must be refused (with a clear
    # message) and the booking left untouched, NOT rescheduled into a same-start successor.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_A), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_resize.fn(state, payload))

    # A clear, domain-honest message (not a coincidental "slot taken"), and no service call at all.
    assert "duración" in state.error.lower()
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_A)
    async with seeded_maker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CONFIRMED  # not rescheduled into a successor


async def test_drag_keeps_the_move_when_the_refresh_fails_after_commit(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If the reschedule COMMITS but the post-commit refresh fails, rolling back would desync the
    # view from the DB (the DB really moved). Keep the optimistic move + a resync notice instead.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    async def _boom(_runtime: object, **_window: object) -> None:
        raise service.AdminSetupError("database unavailable")

    monkeypatch.setattr("aethercal.server.admin.state._fetch_booking_views", _boom)
    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))

    assert state.error != ""  # a resync notice, not a silent success
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_B)  # optimistic move retained
    async with seeded_maker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CANCELLED  # the reschedule really committed


async def test_drag_rolls_back_on_an_unexpected_infra_error(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("connection reset")

    monkeypatch.setattr("aethercal.server.admin.service.reschedule_booking_action", _boom)
    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))

    # An unexpected error before commit must not leave the unconfirmed move applied.
    assert state.error != ""
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_A)  # rolled back to authoritative


async def test_drag_clears_a_stale_selection_after_commit(seeded_maker: Sessionmaker) -> None:
    # A drag reschedule replaces the booking (successor with a new id); a manage panel left open on
    # the old, now-cancelled id would be stale, so a committed reschedule must clear the selection.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)
    AdminState.on_calendar_event_click.fn(state, {"id": str(booking_id)})
    assert state.selected_booking_id == str(booking_id)

    payload = {"id": str(booking_id), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))

    assert state.selected_booking_id == ""  # the panel no longer points at the cancelled booking


async def test_reschedule_selected_uses_the_clicked_booking(seeded_maker: Sessionmaker) -> None:
    # The accessible reschedule path: click selects the booking (no ID typing), then the panel form
    # reschedules the SELECTED booking on a new start.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)
    AdminState.on_calendar_event_click.fn(state, {"id": str(booking_id)})

    await AdminState.reschedule_selected.fn(state, {"new_start": _naive_iso(SLOT_B)})

    assert state.error == ""
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_B)
    assert state.selected_booking_id == ""  # selection cleared after the action


async def test_drag_an_unknown_event_is_a_noop(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    await AdminState.load_bookings.fn(state)
    payload = {"id": str(uuid.uuid4()), "start": _naive_iso(SLOT_B), "end": _naive_iso(SLOT_B)}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))
    assert state.error == ""
    assert state.calendar_events == []


# --------------------------------------------------------------------------------------
# Select → create; click → manage; view switcher.
# --------------------------------------------------------------------------------------


async def test_range_select_opens_the_create_affordance(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    AdminState.on_calendar_range_select.fn(
        state, {"start": _naive_iso(SLOT_A), "end": _naive_iso(SLOT_B), "allDay": False}
    )
    assert state.show_new_booking is True
    assert state.new_booking_start == _naive_iso(SLOT_A)


async def test_event_click_selects_the_booking_to_manage(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    AdminState.on_calendar_event_click.fn(state, {"id": str(booking_id)})
    assert state.selected_booking_id == str(booking_id)
    assert state.selected_booking_start == _naive_iso(SLOT_A)


async def test_set_calendar_view_accepts_valid_and_rejects_invalid(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    AdminState.set_calendar_view.fn(state, "week")
    assert state.calendar_view == "week"
    AdminState.set_calendar_view.fn(state, "bogus")
    assert state.calendar_view == "week"  # unchanged


# --------------------------------------------------------------------------------------
# Period navigation (F2-NAV): prev/next/today move the anchor AND load the visible period.
# --------------------------------------------------------------------------------------


async def test_range_change_navigates_the_anchor_and_loads_the_visible_period(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)

    payload = _month_window(SLOT_A)
    await AdminState.on_calendar_range_change.fn(state, payload)

    assert state.error == ""
    # The anchor moved to the navigated period, and the booking in that period is loaded.
    assert state.calendar_anchor == payload["from"]
    assert [e["id"] for e in state.calendar_events] == [str(booking_id)]


async def test_range_change_scopes_out_events_from_a_different_period(
    seeded_maker: Sessionmaker,
) -> None:
    # The core of "load the events of the VISIBLE period, not everything": a booking two months away
    # is NOT loaded when the operator navigates to a month that does not contain it.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)  # populate the full-history rows first

    other_month = SLOT_A + timedelta(days=62)
    await AdminState.on_calendar_range_change.fn(state, _month_window(other_month))

    # The calendar events are scoped to the navigated (empty) period...
    assert state.calendar_events == []
    # ...but the row list keeps the FULL history (the booking from the other period is still there).
    assert len(state.bookings) == 1


async def test_view_change_sets_the_view_and_loads_the_period(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)

    payload = {**_month_window(SLOT_A), "view": "week"}
    await AdminState.on_calendar_view_change.fn(state, payload)

    assert state.calendar_view == "week"
    assert state.calendar_anchor == payload["from"]
    assert [e["id"] for e in state.calendar_events] == [str(booking_id)]


async def test_view_change_rejects_an_invalid_view_without_navigating(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await AdminState.on_calendar_view_change.fn(state, {**_month_window(SLOT_A), "view": "bogus"})
    assert state.calendar_view == "month"  # unchanged
    assert state.calendar_anchor == ""  # a rejected view never navigates


async def test_reload_applies_only_the_latest_request_for_the_same_range(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Out-of-order guard for the A → B → A shape: two reloads for the SAME period resolve in REVERSE
    # order. The monotonic token must let ONLY the latest request apply — an old, stale response for
    # the same range must not overwrite the fresh one. Anchor/range comparison alone can't catch it.
    state = await _authed_state(seeded_maker)
    state.calendar_anchor = "2026-07-01T00:00:00"
    state.calendar_range_to = "2026-08-01T00:00:00"

    old_may_finish = asyncio.Event()
    call = {"n": 0}

    async def _fetch_reverse_order(_runtime: object, **_window: object) -> list[dict[str, str]]:
        call["n"] += 1
        if call["n"] == 1:
            # The OLD reload: hold until the NEWER one has finished, then return stale data.
            await old_may_finish.wait()
            return [{"id": "OLD", "title": "OLD", "start": "s", "end": "e"}]
        try:
            return [{"id": "NEW", "title": "NEW", "start": "s", "end": "e"}]
        finally:
            old_may_finish.set()  # release the old fetch so it resolves LAST

    monkeypatch.setattr(state_mod, "_fetch_calendar_events", _fetch_reverse_order)

    old = asyncio.create_task(state_mod._reload_calendar(state))
    new = asyncio.create_task(state_mod._reload_calendar(state))
    await asyncio.gather(old, new)

    # The latest request wins; the stale same-range response is dropped.
    assert [e["id"] for e in state.calendar_events] == ["NEW"]


async def test_mutation_refetch_is_dropped_when_a_navigation_supersedes_it(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A mutation's authoritative refetch shares the token guard: if a navigation moved the visible
    # period while the mutation's DB round-trip was in flight, the (stale-window) refetch is dropped
    # instead of overwriting the newer period's events.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    nav_events = [{"id": "NAV", "title": "NAV", "start": "s", "end": "e"}]

    async def _fetch_overtaken_by_navigation(
        _runtime: object, **_window: object
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        # A newer navigation reload lands (bumps the token + applies its events AND its error) while
        # this mutation refetch is resolving; the mutation's result is now stale.
        state.calendar_reload_seq += 1
        state.calendar_events = nav_events
        state.error = "newer-navigation-owns-this"
        return ([], [{"id": str(booking_id), "title": "STALE", "start": "s", "end": "e"}])

    monkeypatch.setattr(state_mod, "_fetch_booking_views", _fetch_overtaken_by_navigation)
    await AdminState.cancel.fn(state, str(booking_id))

    # The mutation succeeded, but its stale refetch was dropped: neither the events nor the error of
    # the newer navigation are clobbered by the superseded mutation.
    assert [e["id"] for e in state.calendar_events] == ["NAV"]
    assert state.error == "newer-navigation-owns-this"


async def test_mutation_applies_history_rows_even_when_navigation_supersedes_the_events(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rows have INDEPENDENT causality from the visible events: a navigation supersedes the events
    # refresh but never touches the rows, so the mutation's row update must still be applied.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)

    fresh_rows = [{"id": "ROW-1", "guest": "fresh"}]

    async def _fetch_overtaken_by_nav_events_only(
        _runtime: object, **_window: object
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        # A newer navigation bumps ONLY the events token (as _reload_calendar does) mid-flight.
        state.calendar_reload_seq += 1
        state.calendar_events = [{"id": "NAV", "title": "NAV", "start": "s", "end": "e"}]
        return (fresh_rows, [{"id": "STALE", "title": "STALE", "start": "s", "end": "e"}])

    monkeypatch.setattr(state_mod, "_fetch_booking_views", _fetch_overtaken_by_nav_events_only)
    await AdminState.cancel.fn(state, str(booking_id))

    # The events refresh was superseded (the navigation's events stand)...
    assert [e["id"] for e in state.calendar_events] == ["NAV"]
    # ...but the mutation's history rows were applied (navigation never owns the rows).
    assert [r["id"] for r in state.bookings] == ["ROW-1"]


async def test_navigation_does_not_reload_the_full_booking_history(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Perf: navigating between periods refreshes ONLY the visible events, never re-queries the full
    # booking history — so `_fetch_booking_views` (rows + events) must not be called on navigation.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)
    history_before = list(state.bookings)
    assert history_before  # the full-history rows were loaded on-load

    async def _must_not_be_called(*_a: object, **_k: object) -> object:
        raise AssertionError("navigation must not re-query the full booking history")

    monkeypatch.setattr(state_mod, "_fetch_booking_views", _must_not_be_called)
    await AdminState.on_calendar_range_change.fn(state, _month_window(SLOT_A))

    # Navigation left the history rows in place (it used the events-only path, not a full reload).
    assert state.bookings == history_before


def test_window_is_the_exact_month_grid_extent() -> None:
    # The month window is the exact 6-week Monday-first grid (period != data range), so every
    # visible adjacent-month cell loads. July 2026 starts on a Wed → grid Mon Jun 29..Sun Aug 9.
    state = _state()
    state.calendar_view = "month"
    state.calendar_anchor = "2026-07-01T00:00:00"
    state.calendar_range_to = "2026-08-01T00:00:00"
    window = state_mod._window(state)
    assert window["date_from"] == date(2026, 6, 29)
    assert window["date_to"] == date(2026, 8, 9)  # 42 days: 2026-06-29 + 41


def test_window_covers_deep_trailing_spillover_of_a_short_february() -> None:
    # A 28-day February that begins on a Sunday pushes the 6-week Monday-first grid to show EIGHT
    # trailing days of March; the window must include the 8th (a fixed 7-day margin would miss it).
    # Feb 2026 starts on Sunday (2026-02-01): grid = Mon Jan 26 .. Sun Mar 8.
    state = _state()
    state.calendar_view = "month"
    state.calendar_anchor = "2026-02-01T00:00:00"
    state.calendar_range_to = "2026-03-01T00:00:00"
    window = state_mod._window(state)
    assert window["date_from"] == date(2026, 1, 26)
    assert window["date_to"] == date(
        2026, 3, 8
    )  # Feb 28 is Sat; the 8th trailing day (Mar 8) loads


def test_window_is_exact_for_non_grid_views() -> None:
    # week / day have no adjacent-period spillover, so the window is the exact period (no padding).
    state = _state()
    state.calendar_view = "week"
    state.calendar_anchor = "2026-07-13T00:00:00"
    state.calendar_range_to = "2026-07-20T00:00:00"
    window = state_mod._window(state)
    assert window["date_from"] == date(2026, 7, 13)
    assert window["date_to"] == date(2026, 7, 19)  # exclusive 'to' -> last inclusive day


async def test_nav_handlers_are_noops_when_unauthenticated() -> None:
    state = _state()  # unauthenticated; runtime deliberately unconfigured
    await AdminState.on_calendar_range_change.fn(state, _month_window(SLOT_A))
    await AdminState.on_calendar_view_change.fn(state, {**_month_window(SLOT_A), "view": "week"})
    assert state.calendar_anchor == ""
    assert state.calendar_view == "month"
    assert state.calendar_events == []


async def test_create_booking_closes_the_form_when_the_refresh_fails_after_commit(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The booking is created, but the follow-up refresh fails. To avoid an ambiguous re-submit, the
    # create form is closed and a resync notice shown — the booking really persisted.
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = state.event_types[0]["id"]
    await AdminState.load_bookings.fn(state)
    state.show_new_booking = True

    async def _boom(_runtime: object, **_window: object) -> None:
        raise service.AdminSetupError("database unavailable")

    monkeypatch.setattr("aethercal.server.admin.state._fetch_booking_views", _boom)
    await AdminState.create_booking.fn(
        state,
        {
            "event_type_id": event_type_id,
            "start": _naive_iso(SLOT_A),
            "guest_name": "Grace Hopper",
            "guest_email": "grace@example.com",
        },
    )

    assert state.error != ""
    assert state.show_new_booking is False  # closed to prevent a duplicate submit
    async with seeded_maker() as session:
        bookings = list((await session.scalars(select(Booking))).all())
        assert len(bookings) == 1
        assert bookings[0].status is BookingStatus.CONFIRMED  # it really committed


async def test_reschedule_selected_clears_selection_when_refresh_fails_after_commit(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    booking_id = await _book_at(seeded_maker, event_type_id=event_type_id, start=SLOT_A)
    await AdminState.load_bookings.fn(state)
    AdminState.on_calendar_event_click.fn(state, {"id": str(booking_id)})

    async def _boom(_runtime: object, **_window: object) -> None:
        raise service.AdminSetupError("database unavailable")

    monkeypatch.setattr("aethercal.server.admin.state._fetch_booking_views", _boom)
    await AdminState.reschedule_selected.fn(state, {"new_start": _naive_iso(SLOT_B)})

    assert state.error != ""
    assert state.selected_booking_id == ""  # stale panel closed
    async with seeded_maker() as session:
        original = await session.get(Booking, booking_id)
        assert original is not None
        assert original.status is BookingStatus.CANCELLED  # the reschedule committed


async def test_create_booking_from_the_calendar_persists(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = state.event_types[0]["id"]
    await AdminState.load_bookings.fn(state)

    await AdminState.create_booking.fn(
        state,
        {
            "event_type_id": event_type_id,
            "start": _naive_iso(SLOT_A),
            "guest_name": "Grace Hopper",
            "guest_email": "grace@example.com",
            "guest_timezone": "UTC",
        },
    )
    assert state.error == ""
    assert state.show_new_booking is False
    assert [e["title"] for e in state.calendar_events] == ["Grace Hopper"]
    assert state.calendar_events[0]["start"] == _naive_iso(SLOT_A)


# --------------------------------------------------------------------------------------
# Service reuse: create_booking_action.
# --------------------------------------------------------------------------------------


async def test_create_booking_action_reuses_the_domain_service(seeded_maker: Sessionmaker) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])

    read = await service.create_booking_action(
        _admin(seeded_maker),
        tenant_slug=None,
        form=service.BookingForm(
            event_type_id=event_type_id,
            start=SLOT_A,
            guest_name="Grace",
            guest_email="grace@example.com",
        ),
    )
    assert read.status is BookingStatus.CONFIRMED
    assert read.start.replace(tzinfo=UTC) == SLOT_A


async def test_create_booking_action_maps_an_off_hours_slot_to_an_action_error(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authed_state(seeded_maker)
    await _seed_event_type(state)
    event_type_id = uuid.UUID(state.event_types[0]["id"])
    with pytest.raises(service.AdminActionError):
        await service.create_booking_action(
            _admin(seeded_maker),
            tenant_slug=None,
            form=service.BookingForm(
                event_type_id=event_type_id,
                start=OFF_HOURS,
                guest_name="Grace",
                guest_email="grace@example.com",
            ),
        )


# --------------------------------------------------------------------------------------
# Auth guards (RF-18): every new handler is a no-op when unauthenticated.
# --------------------------------------------------------------------------------------


async def test_calendar_handlers_are_noops_when_unauthenticated() -> None:
    state = _state()  # unauthenticated; runtime deliberately unconfigured
    payload = {"id": "x", "start": "x", "end": "x"}
    await _drain(AdminState.on_calendar_event_drop.fn(state, payload))
    await _drain(AdminState.on_calendar_event_resize.fn(state, payload))
    AdminState.on_calendar_range_select.fn(state, {"start": "x", "end": "x", "allDay": False})
    AdminState.on_calendar_event_click.fn(state, {"id": "x"})
    AdminState.set_calendar_view.fn(state, "week")
    await AdminState.create_booking.fn(state, {})
    assert state.calendar_events == []
    assert state.show_new_booking is False
    assert state.selected_booking_id == ""
    assert state.calendar_view == "month"  # default unchanged (guarded)
