"""Offline tests for the app factory: wiring, health, and the generic 401 (F1 foundation).

These use an in-memory aiosqlite URL and an ASGITransport client (which never runs the lifespan),
proving the eagerly-built engine/sessionmaker on ``app.state`` are what serve requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from aethercal.server import app as app_module
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.app import build_email_sender, create_app, create_app_from_env
from aethercal.server.integrations.smtp.sender import SmtpEmailSender
from aethercal.server.settings import Settings


def _offline_app() -> FastAPI:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        app_secret="test-secret",
        auto_migrate=False,
    )
    app = create_app(settings)

    @app.get("/api/v1/_probe/whoami")
    async def _whoami(ctx: Annotated[AuthContext, Depends(require_api_key)]) -> dict[str, str]:
        return {"tenant_id": str(ctx.tenant_id)}

    return app


@pytest_asyncio.fixture
async def offline_client() -> AsyncIterator[AsyncClient]:
    app = _offline_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    await app.state.engine.dispose()


async def test_health_is_ok(offline_client: AsyncClient) -> None:
    resp = await offline_client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_engine_and_sessionmaker_are_built_eagerly() -> None:
    app = _offline_app()
    try:
        assert app.state.engine is not None
        assert app.state.sessionmaker is not None
    finally:
        await app.state.engine.dispose()


async def test_missing_auth_returns_generic_401(offline_client: AsyncClient) -> None:
    resp = await offline_client.get("/api/v1/_probe/whoami")
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized", "message": "Invalid or missing API key"}
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_malformed_key_returns_the_same_generic_401(offline_client: AsyncClient) -> None:
    resp = await offline_client.get(
        "/api/v1/_probe/whoami", headers={"Authorization": "Bearer not-a-valid-key"}
    )
    assert resp.status_code == 401
    # Identical body to the missing-header case: the response never reveals *why* it failed.
    assert resp.json() == {"error": "unauthorized", "message": "Invalid or missing API key"}


# --------------------------------------------------------------------------------------
# create_app_from_env — the uvicorn --factory entrypoint reads Settings from the environment.
# --------------------------------------------------------------------------------------


async def test_create_app_from_env_builds_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "env-secret")
    monkeypatch.setenv("AETHERCAL_AUTO_MIGRATE", "false")

    app = create_app_from_env()
    try:
        assert app.state.settings.app_secret == "env-secret"
        assert app.state.settings.auto_migrate is False
        assert app.state.engine is not None
    finally:
        await app.state.engine.dispose()


# --------------------------------------------------------------------------------------
# build_email_sender — an SmtpEmailSender only when SMTP is configured; None otherwise (RF-19).
# --------------------------------------------------------------------------------------


def test_build_email_sender_is_none_without_smtp_config() -> None:
    assert build_email_sender({}) is None


def test_build_email_sender_builds_a_sender_when_smtp_is_configured() -> None:
    sender = build_email_sender(
        {"AETHERCAL_SMTP_HOST": "smtp.example.com", "AETHERCAL_SMTP_FROM": "no-reply@example.com"}
    )
    assert isinstance(sender, SmtpEmailSender)


# --------------------------------------------------------------------------------------
# Lifespan — attaches the shared runtime effects the booking flow reads best-effort (F1-12).
# --------------------------------------------------------------------------------------


async def test_lifespan_attaches_runtime_effects_without_a_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No SMTP env and the scheduler off: http_client + fernet_key are present, the two optional
    # effects are None, and nothing is hard-failed on the missing SMTP/Google config.
    for var in ("AETHERCAL_SMTP_HOST", "AETHERCAL_SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        app_secret="test-secret",
        auto_migrate=False,
        run_scheduler=False,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.http_client, httpx.AsyncClient)
        assert app.state.fernet_key == settings.fernet_key()
        assert app.state.email_sender is None
        assert app.state.scheduler is None

    # The HTTP client is closed cleanly on shutdown (no leaked resources under filterwarnings).
    assert app.state.http_client.is_closed is True


async def test_lifespan_unwinds_every_started_resource_on_partial_boot_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure PART-WAY through startup must still tear down everything already acquired.

    The scheduler wiring is exercised with fakes (the live APScheduler objects stay behind their
    ``# pragma: no cover - live`` seam). ``start_scheduler`` blows up AFTER the reminder runner has
    started, so the ``AsyncExitStack`` must unwind every resource registered up to that point — the
    reminder runner is shut down, the HTTP client closed, and the engine disposed — rather than
    leaking them (the old normal-shutdown-only path never ran cleanup on a partial boot).
    """
    for var in ("AETHERCAL_SMTP_HOST", "AETHERCAL_SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(
        database_url="sqlite+aiosqlite://",
        app_secret="test-secret",
        auto_migrate=False,
        run_scheduler=True,
    )

    class _FakeEngine:
        """Records disposal so the test can prove the engine's cleanup ran on partial boot."""

        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    def _boom_start_scheduler(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("scheduler failed to start")

    monkeypatch.setattr(app_module, "build_async_engine", lambda _config: fake_engine)
    monkeypatch.setattr(app_module, "build_sessionmaker", lambda _engine: object())
    monkeypatch.setattr(app_module, "build_interval_scheduler", object)
    monkeypatch.setattr(app_module, "start_scheduler", _boom_start_scheduler)

    app = create_app(settings)

    # Startup raises before the lifespan yields; the AsyncExitStack must still unwind on exit.
    with pytest.raises(RuntimeError, match="scheduler failed to start"):
        async with app.router.lifespan_context(app):
            pass  # unreachable — the failure happens during startup

    # The engine and the HTTP client were BOTH acquired before the scheduler blew up, and the
    # AsyncExitStack still unwound every one of them: client closed, engine disposed. (There is no
    # reminder runner to unwind any more — the outbox is the durable scheduler now.)
    assert app.state.http_client.is_closed is True
    assert fake_engine.disposed is True


async def test_lifespan_disposes_the_async_engine_when_the_boot_migration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot migration that raises must not strand the async engine's connection pool.

    The async engine is built eagerly in ``create_app`` (before the lifespan runs), so its teardown
    has to be registered on the ``AsyncExitStack`` BEFORE the boot migration — the earliest startup
    step that can fail. Registering it only after the migration leaks the pool on the one startup
    path most likely to blow up in production: a bad or blocked Alembic upgrade.
    """
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        app_secret="test-secret",
        auto_migrate=True,
        run_scheduler=False,
    )

    class _FakeEngine:
        """Records disposal so the test can prove the async engine's cleanup ran."""

        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class _FakeSyncEngine:
        """The throwaway sync engine the Alembic boot migrator runs on."""

        def __init__(self) -> None:
            self.disposed = False

        def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()
    fake_sync_engine = _FakeSyncEngine()

    def _boom_run_migrations(_sync_engine: Any) -> None:
        raise RuntimeError("alembic upgrade failed")

    monkeypatch.setattr(app_module, "build_async_engine", lambda _config: fake_engine)
    monkeypatch.setattr(app_module, "build_sessionmaker", lambda _engine: object())
    monkeypatch.setattr(app_module, "build_sync_engine", lambda _config: fake_sync_engine)
    monkeypatch.setattr(app_module, "run_migrations", _boom_run_migrations)

    app = create_app(settings)

    # The boot migration is the FIRST startup step; it raises before anything else is acquired.
    with pytest.raises(RuntimeError, match="alembic upgrade failed"):
        async with app.router.lifespan_context(app):
            pass  # unreachable — the failure happens during startup

    # The throwaway sync engine was always disposed (its own try/finally), but the async engine's
    # pool must be released too — that is the leak this guards against.
    assert fake_sync_engine.disposed is True
    assert fake_engine.disposed is True
