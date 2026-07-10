"""Tests for the admin mount + app assembly (F1-11, RF-18).

The mount is additive and off-by-default: the plain server never grows an ``/admin`` route unless
the operator configures credentials AND enables it. These tests cover the gating decision (without
standing up the Reflex frontend, which needs Node) and prove the Reflex app assembles its four pages
offline — which exercises every page render function and state binding.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aethercal.server.admin import mount as mount_module
from aethercal.server.admin.app import build_admin_app
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.mount import ADMIN_MOUNT_PATH, mount_admin
from aethercal.server.admin.runtime import AdminRuntime
from aethercal.server.app import create_app
from aethercal.server.settings import Settings

_ENABLED_ENV = {
    "AETHERCAL_ADMIN_USERNAME": "admin",
    "AETHERCAL_ADMIN_PASSWORD_HASH": "pbkdf2_sha256$1$aa$bb",
    "AETHERCAL_ADMIN_ENABLED": "1",
}


def test_mount_is_a_noop_when_unconfigured() -> None:
    app = FastAPI()
    before = list(app.router.routes)
    assert mount_admin(app, environ={}) is False
    assert list(app.router.routes) == before


def test_mount_is_a_noop_when_configured_but_not_enabled() -> None:
    app = FastAPI()
    env = {k: v for k, v in _ENABLED_ENV.items() if k != "AETHERCAL_ADMIN_ENABLED"}
    assert mount_admin(app, environ=env) is False


def test_mount_invokes_the_live_mount_when_configured_and_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub the live mount (it would need the built Reflex frontend) and prove the gate opens.
    recorded: list[tuple[FastAPI, AdminConfig]] = []

    def _record(app: FastAPI, config: AdminConfig) -> None:
        recorded.append((app, config))

    monkeypatch.setattr(mount_module, "_mount_admin_app", _record)

    app = FastAPI()
    assert mount_admin(app, environ=_ENABLED_ENV) is True
    assert len(recorded) == 1
    _, config = recorded[0]
    assert config.username == "admin"


async def test_create_app_does_not_mount_admin_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENABLED_ENV:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        app_secret="test-secret",
        auto_migrate=False,
    )
    app = create_app(settings)
    try:
        mounted = [route for route in app.routes if getattr(route, "path", "") == ADMIN_MOUNT_PATH]
        assert mounted == []
    finally:
        await app.state.engine.dispose()


def test_build_admin_app_registers_the_four_pages() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    runtime = AdminRuntime(
        sessionmaker=async_sessionmaker(engine),
        config=AdminConfig(username="admin", password_hash="x", tenant_slug=None),
    )
    app = build_admin_app(runtime)
    assert set(app._unevaluated_pages) == {"index", "login", "event-types", "schedules"}
