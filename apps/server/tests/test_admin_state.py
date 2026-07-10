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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.admin import runtime as runtime_mod
from aethercal.server.admin.config import AdminConfig
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
    (AdminState.reschedule.fn, ({"booking_id": "x", "new_start": "x"},)),
    (AdminState.load_event_types.fn, ()),
    (AdminState.create_event_type.fn, ({},)),
    (AdminState.update_event_type.fn, ({},)),
    (AdminState.deactivate_event_type.fn, ("00000000-0000-0000-0000-000000000000",)),
    (AdminState.load_schedules.fn, ()),
    (AdminState.create_schedule.fn, ({},)),
    (AdminState.update_schedule.fn, ({},)),
    (AdminState.delete_schedule.fn, ("00000000-0000-0000-0000-000000000000",)),
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


def test_authenticated_is_a_server_only_backend_var() -> None:
    # Not shipped to the frontend and no generated client setter → a client cannot flip it.
    assert "_authenticated" in AdminState.backend_vars
    assert "authenticated" not in getattr(AdminState, "vars", {})
    assert "set__authenticated" not in dir(AdminState)


def test_fresh_state_is_unauthenticated() -> None:
    assert _state()._authenticated is False


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
