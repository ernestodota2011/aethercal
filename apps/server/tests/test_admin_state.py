"""Tests for the admin Reflex state's authorization (F1-11, RF-18).

The security-critical invariant: NO event handler reads or mutates tenant data unless the session is
authenticated, and the ``_authenticated`` flag cannot be set from the client. A page ``on_load``
guard alone is not enough — a client can invoke any handler directly over the websocket — so every
handler is tested here by calling its raw coroutine (``Handler.fn(state)``) on an unauthenticated
state and asserting it is a no-op that never even reaches the runtime/service.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from reflex.event import EventHandler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.admin import runtime as runtime_mod
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.format import SHARED_SCHEDULE
from aethercal.server.admin.passwords import hash_password
from aethercal.server.admin.ratelimit import LOGIN_LIMITER, PBKDF2_LIMITER
from aethercal.server.admin.runtime import AdminRuntime, configure_runtime
from aethercal.server.admin.state import AdminState
from aethercal.server.db import Base
from aethercal.server.db.models import Tenant, User

Sessionmaker = async_sessionmaker[AsyncSession]

# Every data handler + a representative argument (unauth returns before the arg is ever used).
_GUARDED: list[tuple[Callable[..., Awaitable[None]], tuple[object, ...]]] = [
    (AdminState.load_bookings.fn, ()),
    (AdminState.cancel.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.mark_no_show.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.reschedule.fn, ({"booking_id": "x", "new_start": "x"},)),
    (AdminState.load_event_types.fn, ()),
    (AdminState.create_event_type.fn, ({},)),
    (AdminState.update_event_type.fn, ({},)),
    (AdminState.deactivate_event_type.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.load_schedules.fn, ()),
    (AdminState.create_schedule.fn, ({},)),
    (AdminState.update_schedule.fn, ({},)),
    (AdminState.delete_schedule.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.load_metrics.fn, ()),
    (AdminState.load_branding.fn, ()),
    (AdminState.save_branding.fn, ({},)),
    (AdminState.load_hosts.fn, ()),
    (AdminState.create_host.fn, ({},)),
    (AdminState.update_host.fn, ({},)),
    (AdminState.delete_host.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.select_host.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.designate_calendar.fn, ({},)),
    (AdminState.load_workflows.fn, ()),
    (AdminState.create_workflow.fn, ({},)),
    (AdminState.update_workflow.fn, ({},)),
    (AdminState.activate_workflow.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.deactivate_workflow.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.create_template.fn, ({},)),
    (AdminState.update_template.fn, ({},)),
    (AdminState.delete_template.fn, ("00000000-0000-0000-0000-000000000000",)),
]


@pytest.fixture(autouse=True)
def _clean_runtime() -> AsyncIterator[None]:
    """Reset the process-global runtime + login limiter around each test (no cross-test bleed)."""
    saved = runtime_mod._Holder.value
    runtime_mod._Holder.value = None
    LOGIN_LIMITER.reset()
    yield
    runtime_mod._Holder.value = saved
    LOGIN_LIMITER.reset()


@pytest_asyncio.fixture
async def seeded_maker() -> AsyncIterator[Sessionmaker]:
    """An in-memory sessionmaker with one tenant + host user (so authed reads resolve)."""
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


async def _seeded_host_id(state: AdminState) -> str:
    """The tenant's ONE host, as a form value.

    RF-30 made the host an EXPLICIT field on the event-type form. It used to be injected by the
    service — the tenant's first user — which is precisely why a business's second host could never
    be given an event type. So every create now states which host it means, these tests included.
    """
    await AdminState.load_hosts.fn(state)
    assert len(state.hosts) == 1, "these tests seed a single-host tenant"
    return state.hosts[0]["id"]


def test_authenticated_is_a_server_only_backend_var() -> None:
    # Not shipped to the frontend and no generated client setter → a client cannot flip it.
    assert "_authenticated" in AdminState.backend_vars
    assert "authenticated" not in getattr(AdminState, "vars", {})
    assert "set__authenticated" not in dir(AdminState)


#: Handlers that are PUBLIC by design: they are the auth surface itself.
_PUBLIC_HANDLERS = frozenset({"login", "logout", "require_auth", "setvar"})

#: Handlers that read and write NO tenant data — they only close a panel in the operator's own
#: browser. They need no guard because there is nothing behind them to guard.
_UI_ONLY_HANDLERS = frozenset({"clear_selection", "close_new_booking"})

#: Calendar handlers whose unauthenticated no-op is proven in ``test_admin_calendar.py`` (they take
#: gesture payloads, and two of them are async generators, so they are exercised there rather than
#: in the parametrised sweep below).
_GUARDED_ELSEWHERE = frozenset(
    {
        "on_calendar_event_drop",
        "on_calendar_event_resize",
        "on_calendar_range_select",
        "on_calendar_event_click",
        "on_calendar_range_change",
        "on_calendar_view_change",
        "set_calendar_view",
        "create_booking",
        "reschedule_selected",
    }
)


def test_fresh_state_is_unauthenticated() -> None:
    assert _state()._authenticated is False


def test_no_handler_can_skip_the_auth_census_unnoticed() -> None:
    """==Every event handler on the state is CLASSIFIED, or this test fails.==

    Reflex exposes each ``@rx.event`` over the websocket, so a client can invoke any of them
    directly — a page's ``on_load`` guard protects nothing. The proof that a handler refuses an
    unauthenticated caller therefore has to exist for EVERY handler, and until now the lists that
    carry those proofs were maintained by hand: a handler added to ``state.py`` and forgotten was a
    handler nobody had ever proven guarded, and nothing said so. The hole announced itself exactly
    the way this project's defects always do — by being silent.

    So the census is derived from the CLASS, not from a list somebody must remember to update. A new
    handler is UNCLASSIFIED until its author decides which of the four it is, and an unclassified
    handler fails here.
    """
    declared = {name for name, value in vars(AdminState).items() if isinstance(value, EventHandler)}
    classified = (
        {handler.__name__ for handler, _ in _GUARDED}
        | _PUBLIC_HANDLERS
        | _UI_ONLY_HANDLERS
        | _GUARDED_ELSEWHERE
    )
    unclassified = declared - classified
    assert unclassified == set(), (
        f"event handlers with no auth-guard proof: {sorted(unclassified)}. Add each to _GUARDED "
        "and prove it refuses an unauthenticated caller — or, if it touches no tenant data at all, "
        "to _UI_ONLY_HANDLERS."
    )


@pytest.mark.parametrize("handler", [h for h, _ in _GUARDED], ids=lambda h: h.__name__)
async def test_handlers_are_a_noop_when_unauthenticated(
    handler: Callable[..., Awaitable[None]],
) -> None:
    # The runtime is deliberately UNCONFIGURED. If a handler skipped its auth guard it would call
    # current_runtime() and raise — so "does not raise + data untouched" proves the guard holds.
    args = next(a for h, a in _GUARDED if h is handler)
    state = _state()
    await handler(state, *args)
    assert state.bookings == []
    assert state.event_types == []
    assert state.schedules == []


async def test_login_authenticates_on_correct_credentials(seeded_maker: Sessionmaker) -> None:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()

    redirect = await AdminState.login.fn(state, {"username": "operator", "password": "s3cret"})
    assert state._authenticated is True
    assert state.error == ""
    assert redirect is not None  # a redirect EventSpec home


async def test_login_rejects_wrong_credentials(seeded_maker: Sessionmaker) -> None:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()

    redirect = await AdminState.login.fn(state, {"username": "operator", "password": "wrong"})
    assert state._authenticated is False
    assert state.error != ""
    assert redirect is None


async def test_login_lockout_survives_a_new_session(seeded_maker: Sessionmaker) -> None:
    # The whole point of the process-level limiter: five failures across FIVE DIFFERENT sessions
    # (fresh state each time, same client IP) still trip the lock — a new session does not reset it.
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))

    for _ in range(5):
        session = _state()  # a brand-new websocket session per attempt
        await AdminState.login.fn(session, {"username": "operator", "password": "wrong"})

    # A sixth, brand-new session with the CORRECT password is still refused — the limiter gates
    # before verification, and its budget is per-IP/username at the process level, not per-session.
    fresh = _state()
    redirect = await AdminState.login.fn(fresh, {"username": "operator", "password": "s3cret"})
    assert redirect is None
    assert fresh._authenticated is False
    assert "Too many attempts" in fresh.error


async def test_authenticated_load_reaches_the_service(seeded_maker: Sessionmaker) -> None:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()
    state._authenticated = True

    await AdminState.load_bookings.fn(state)
    # The (empty) tenant resolves cleanly: the query ran, no setup error surfaced.
    assert state.bookings == []
    assert state.error == ""


async def test_reschedule_stamps_a_naive_datetime_local_as_utc(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()
    state._authenticated = True

    captured: dict[str, datetime] = {}

    async def _spy(*_args: object, new_start: datetime, **_kwargs: object) -> None:
        captured["new_start"] = new_start

    monkeypatch.setattr("aethercal.server.admin.service.reschedule_booking_action", _spy)

    await AdminState.reschedule.fn(
        state,
        {"booking_id": "00000000-0000-0000-0000-000000000001", "new_start": "2026-07-06T11:00"},
    )
    assert captured["new_start"] == datetime(2026, 7, 6, 11, 0, tzinfo=UTC)


async def test_concurrent_failed_logins_do_not_blow_past_the_budget(
    seeded_maker: Sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A burst of concurrent wrong-password logins (distinct sessions, same IP) must not run far more
    # PBKDF2 than the budget: once five fail, the in-slot re-check aborts the rest, so the overshoot
    # is bounded by the concurrency limit — not the full burst.
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))

    lock = threading.Lock()
    calls = 0

    def _count(_config: object, _username: str, _password: str) -> bool:
        nonlocal calls
        with lock:
            calls += 1
        return False

    monkeypatch.setattr("aethercal.server.admin.state.authenticate", _count)

    await asyncio.gather(
        *(
            AdminState.login.fn(_state(), {"username": "operator", "password": "x"})
            for _ in range(20)
        )
    )
    assert calls < 20  # the burst did not all reach verification
    assert calls <= 5 + PBKDF2_LIMITER.limit  # overshoot bounded by concurrency
    assert LOGIN_LIMITER.any_locked(["ip:unknown", "user:operator"]) is True


async def test_deactivating_an_unknown_event_type_reports_not_found(
    seeded_maker: Sessionmaker,
) -> None:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()
    state._authenticated = True

    await AdminState.deactivate_event_type.fn(state, str(uuid.uuid4()))
    assert state.error == "Event type not found"


# --------------------------------------------------------------------------------------
# Event-type EN translations (A4, bilingual C1 admin follow-up).
# --------------------------------------------------------------------------------------

_WEEKLY_SCHEDULE_FORM = {
    "name": "Weekly",
    "timezone": "UTC",
    "weekdays": "0,1,2,3,4",
    "start": "09:00",
    "end": "17:00",
}


async def _authenticated_state(seeded_maker: Sessionmaker) -> AdminState:
    config = AdminConfig(
        username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
    )
    configure_runtime(AdminRuntime(sessionmaker=seeded_maker, config=config))
    state = _state()
    state._authenticated = True
    return state


async def test_create_event_type_saves_the_en_translations(seeded_maker: Sessionmaker) -> None:
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)

    await AdminState.create_event_type.fn(
        state,
        {
            "host_id": await _seeded_host_id(state),
            "slug": "intro",
            "title": "Introducción",
            "schedule": "Weekly",
            "duration_min": "30",
            "max_advance_days": "30",
            "title_en": "Discovery call",
            "description_en": "A quick intro.",
        },
    )

    assert state.error == ""
    assert state.event_types[0]["title_en"] == "Discovery call"
    assert state.event_types[0]["description_en"] == "A quick intro."


async def test_create_event_type_with_blank_en_fields_does_not_store_the_key(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)

    await AdminState.create_event_type.fn(
        state,
        {
            "host_id": await _seeded_host_id(state),
            "slug": "intro",
            "title": "Introducción",
            "schedule": "Weekly",
            "duration_min": "30",
            "max_advance_days": "30",
            "title_en": "",
            "description_en": "   ",
        },
    )

    assert state.error == ""
    assert state.event_types[0]["title_en"] == ""
    assert state.event_types[0]["description_en"] == ""


async def test_update_event_type_sets_the_en_translation_and_reload_populates_it(
    seeded_maker: Sessionmaker,
) -> None:
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
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
    event_type_id = state.event_types[0]["id"]

    await AdminState.update_event_type.fn(
        state, {"id": event_type_id, "title_en": "Discovery call"}
    )

    assert state.error == ""
    assert state.event_types[0]["title_en"] == "Discovery call"
    # The canonical title, untouched by this request, must survive (see the no-op test below for
    # why this matters: a blank field must be OMITTED, never sent as an explicit ``None``).
    assert state.event_types[0]["title"] == "Introducción"


async def test_update_event_type_absent_en_field_leaves_the_existing_translation_untouched(
    seeded_maker: Sessionmaker,
) -> None:
    # An update payload that does NOT carry the EN field at all leaves the stored translation as-is
    # (presence in the form is the signal; absence = "don't touch this field").
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
    await AdminState.create_event_type.fn(
        state,
        {
            "host_id": await _seeded_host_id(state),
            "slug": "intro",
            "title": "Introducción",
            "schedule": "Weekly",
            "duration_min": "30",
            "max_advance_days": "30",
            "title_en": "Discovery call",
        },
    )
    event_type_id = state.event_types[0]["id"]

    await AdminState.update_event_type.fn(state, {"id": event_type_id, "duration_min": "45"})

    assert state.error == ""
    assert state.event_types[0]["duration_min"] == "45"
    assert state.event_types[0]["title_en"] == "Discovery call"


async def _event_with_en_translations(seeded_maker: Sessionmaker) -> tuple[AdminState, str]:
    """A seeded, authed state + one event type that already has EN title/description overrides."""
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
    await AdminState.create_event_type.fn(
        state,
        {
            "host_id": await _seeded_host_id(state),
            "slug": "intro",
            "title": "Introducción",
            "schedule": "Weekly",
            "duration_min": "30",
            "max_advance_days": "30",
            "title_en": "Discovery call",
            "description_en": "A quick intro.",
        },
    )
    assert state.event_types[0]["title_en"] == "Discovery call"
    return state, state.event_types[0]["id"]


async def test_update_event_type_editing_only_duration_preserves_existing_en_translations(
    seeded_maker: Sessionmaker,
) -> None:
    # THE regression Crisol caught: the real edit form always submits the EN inputs (blank when the
    # operator only changed duration). A blank EN field must NOT silently drop a saved translation.
    state, event_type_id = await _event_with_en_translations(seeded_maker)

    await AdminState.update_event_type.fn(
        state,
        {"id": event_type_id, "duration_min": "45", "title_en": "", "description_en": ""},
    )

    assert state.error == ""
    assert state.event_types[0]["duration_min"] == "45"
    assert state.event_types[0]["title_en"] == "Discovery call"  # PRESERVED, not silently cleared
    assert state.event_types[0]["description_en"] == "A quick intro."


async def test_update_event_type_blank_en_without_clear_checkbox_preserves_translation(
    seeded_maker: Sessionmaker,
) -> None:
    # A blank EN field with the clear checkbox UNCHECKED (absent) preserves the existing override.
    state, event_type_id = await _event_with_en_translations(seeded_maker)

    await AdminState.update_event_type.fn(state, {"id": event_type_id, "title_en": ""})

    assert state.error == ""
    assert state.event_types[0]["title_en"] == "Discovery call"


async def test_update_event_type_clear_checkbox_removes_the_translation(
    seeded_maker: Sessionmaker,
) -> None:
    # Removal is EXPLICIT: only the per-field clear checkbox empties a stored translation ({}).
    state, event_type_id = await _event_with_en_translations(seeded_maker)

    await AdminState.update_event_type.fn(
        state,
        {"id": event_type_id, "clear_title_en": "on", "clear_description_en": "true"},
    )

    assert state.error == ""
    assert state.event_types[0]["title_en"] == ""
    assert state.event_types[0]["description_en"] == ""
    assert state.event_types[0]["title"] == "Introducción"  # canonical untouched


async def test_update_event_type_clear_checkbox_wins_over_a_typed_value(
    seeded_maker: Sessionmaker,
) -> None:
    # If the clear checkbox is checked, the (ignored) input value must not resurrect the override.
    state, event_type_id = await _event_with_en_translations(seeded_maker)

    await AdminState.update_event_type.fn(
        state,
        {"id": event_type_id, "title_en": "Ignored", "clear_title_en": "on"},
    )

    assert state.error == ""
    assert state.event_types[0]["title_en"] == ""


async def test_update_event_type_new_en_value_sets_the_translation_when_not_cleared(
    seeded_maker: Sessionmaker,
) -> None:
    state, event_type_id = await _event_with_en_translations(seeded_maker)

    await AdminState.update_event_type.fn(state, {"id": event_type_id, "title_en": "Intro call"})

    assert state.error == ""
    assert state.event_types[0]["title_en"] == "Intro call"  # updated to the new value


async def test_update_event_type_blank_canonical_title_is_omitted_not_cleared(
    seeded_maker: Sessionmaker,
) -> None:
    # Canonical title/description stay omit-if-blank (NOT NULL in the DB): a present-but-blank
    # canonical field must be a no-op, never an explicit None that would flush as a violation.
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
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
    event_type_id = state.event_types[0]["id"]

    await AdminState.update_event_type.fn(
        state, {"id": event_type_id, "title": "", "duration_min": "45"}
    )

    assert state.error == ""
    assert state.event_types[0]["title"] == "Introducción"  # blank canonical omitted, not cleared
    assert state.event_types[0]["duration_min"] == "45"


async def test_update_event_type_with_only_id_is_a_true_no_op(
    seeded_maker: Sessionmaker,
) -> None:
    # ``title`` is NOT NULL in the DB. Blank optional fields on the update form must be OMITTED
    # from the payload (not sent as an explicit ``None``), or this crashes at flush time — the
    # exact bug fixed alongside A4 while wiring the EN-translation fields through this handler.
    state = await _authenticated_state(seeded_maker)
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
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
    event_type_id = state.event_types[0]["id"]

    await AdminState.update_event_type.fn(state, {"id": event_type_id})

    assert state.error == ""
    assert state.event_types[0]["title"] == "Introducción"
    assert state.event_types[0]["duration_min"] == "30"


# --------------------------------------------------------------------------------------
# A schedule's owner can be MOVED (RF-30). The column existed and nobody could touch it.
# --------------------------------------------------------------------------------------


async def _authed(maker: Sessionmaker) -> AdminState:
    configure_runtime(
        AdminRuntime(
            sessionmaker=maker,
            config=AdminConfig(
                username="operator", password_hash=hash_password("s3cret"), tenant_slug=None
            ),
        )
    )
    state = _state()
    state._authenticated = True
    return state


async def _schedule_owner(state: AdminState, name: str) -> str:
    """The ``owner`` cell of the schedule called ``name`` — the id of its host, or the sentinel."""
    await AdminState.load_schedules.fn(state)
    return next(row["owner"] for row in state.schedules if row["name"] == name)


async def _schedule_id_of(state: AdminState, name: str) -> str:
    await AdminState.load_schedules.fn(state)
    return next(row["id"] for row in state.schedules if row["name"] == name)


async def test_a_schedule_can_be_handed_from_one_host_to_another(
    seeded_maker: Sessionmaker,
) -> None:
    """==The column existed and nobody could move it.==

    ``schedules.user_id`` shipped with RF-30, and the EDIT form never exposed it — so a schedule
    created with an owner could not be transferred, and a shared one could not be assigned. A field
    the database has and the panel cannot reach is a field that does not exist.
    """
    state = await _authed(seeded_maker)
    await AdminState.create_host.fn(
        state, {"name": "Bruno", "email": "bruno@example.com", "timezone": "UTC"}
    )
    await AdminState.load_hosts.fn(state)
    by_name = {row["name"]: row["id"] for row in state.hosts}
    ana, bruno = by_name["Host"], by_name["Bruno"]

    await AdminState.create_schedule.fn(state, {**_WEEKLY_SCHEDULE_FORM, "owner_id": ana})
    assert await _schedule_owner(state, "Weekly") == ana

    await AdminState.update_schedule.fn(
        state, {"id": await _schedule_id_of(state, "Weekly"), "owner_id": bruno}
    )

    assert state.error == ""
    assert await _schedule_owner(state, "Weekly") == bruno


async def test_a_schedule_can_be_handed_back_to_the_whole_business(
    seeded_maker: Sessionmaker,
) -> None:
    """==The sentinel earns its keep here.==

    "I did not touch the field" and "I want this to belong to nobody" cannot be the same value, so
    an untouched (blank) select leaves the owner alone and the explicit ``(business)`` option is
    what clears it. Otherwise editing a schedule's NAME would quietly take it from its host.
    """
    state = await _authed(seeded_maker)
    await AdminState.load_hosts.fn(state)
    ana = state.hosts[0]["id"]
    await AdminState.create_schedule.fn(state, {**_WEEKLY_SCHEDULE_FORM, "owner_id": ana})
    schedule_id = await _schedule_id_of(state, "Weekly")

    await AdminState.update_schedule.fn(state, {"id": schedule_id, "owner_id": SHARED_SCHEDULE})

    assert state.error == ""
    assert await _schedule_owner(state, "Weekly") == SHARED_SCHEDULE


async def test_editing_only_the_name_never_takes_a_schedule_away_from_its_host(
    seeded_maker: Sessionmaker,
) -> None:
    """The blank field PRESERVES the owner. If it meant "shared", renaming a schedule would silently
    hand it to the whole business — and two hosts would come to share a pattern nobody chose."""
    state = await _authed(seeded_maker)
    await AdminState.load_hosts.fn(state)
    ana = state.hosts[0]["id"]
    await AdminState.create_schedule.fn(state, {**_WEEKLY_SCHEDULE_FORM, "owner_id": ana})
    schedule_id = await _schedule_id_of(state, "Weekly")

    await AdminState.update_schedule.fn(state, {"id": schedule_id, "name": "Renamed"})

    assert state.error == ""
    assert await _schedule_owner(state, "Renamed") == ana  # still hers


async def test_a_schedule_cannot_be_given_to_another_businesss_host(
    seeded_maker: Sessionmaker,
) -> None:
    """The owner arrives from a form, so it is a cross-tenant write surface until it is checked."""
    async with seeded_maker() as session, session.begin():
        intruder_tenant = Tenant(slug="beta", name="Beta")
        session.add(intruder_tenant)
        await session.flush()
        intruder = User(
            tenant_id=intruder_tenant.id, email="b@example.com", name="Beto", timezone="UTC"
        )
        session.add(intruder)
        await session.flush()
        intruder_id = str(intruder.id)

    # Two tenants now exist, so the admin must be told which one it administers.
    configure_runtime(
        AdminRuntime(
            sessionmaker=seeded_maker,
            config=AdminConfig(
                username="operator", password_hash=hash_password("s3cret"), tenant_slug="acme"
            ),
        )
    )
    state = _state()
    state._authenticated = True
    await AdminState.create_schedule.fn(state, _WEEKLY_SCHEDULE_FORM)
    schedule_id = await _schedule_id_of(state, "Weekly")

    await AdminState.update_schedule.fn(state, {"id": schedule_id, "owner_id": intruder_id})

    assert state.error != ""
    assert await _schedule_owner(state, "Weekly") == SHARED_SCHEDULE  # untouched


# --------------------------------------------------------------------------------------
# Branding panel (B-07 / RF-27) — at the STATE handler, not just the service.
# --------------------------------------------------------------------------------------


async def test_save_branding_keeps_the_validation_error_visible(
    seeded_maker: Sessionmaker,
) -> None:
    """==A refused save must leave the operator the sentence they can act on.==

    The handler re-loads the form boxes on refusal so they show what is actually stored — but that
    reload clears ``self.error`` the way every ``on_load`` does. Setting the error and THEN
    reloading let the reload swallow it: the panel silently redrew and the operator never learned
    why nothing saved. The error must survive the reload.
    """
    state = await _authenticated_state(seeded_maker)

    await AdminState.save_branding.fn(
        state,
        {
            "public_name": "Acme",
            "logo_url": "",
            "accent_color": "",
            "timezone": "Mars/Phobos",
        },
    )

    assert state.error != ""
    assert "timezone" in state.error
    # ...and the boxes show what is stored (the seed's UTC), not the rejected submission.
    assert state.branding["timezone"] == "UTC"
    assert state.branding["public_name"] == ""
