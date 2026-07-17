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
from aethercal.server.app import (
    build_instance_sender_defaults,
    create_app,
    create_app_from_env,
)
from aethercal.server.integrations.smtp.sender import SmtpEmailSender
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.services.tenant_senders import InstanceSenderDefaults
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
# build_instance_sender_defaults — the OPERATOR's configuration, and NOT a sender (B-03bis).
#
# This replaced `build_email_sender` / `build_channel_senders`, which returned live clients built
# from the instance's environment before any business was known. The process edge now reads inert
# configuration; turning it into a sender needs a `tenant_id` (services/tenant_senders).
# --------------------------------------------------------------------------------------


def test_the_defaults_are_empty_without_smtp_config() -> None:
    """Unconfigured SMTP is still not a boot failure — the contract `build_email_sender` carried."""
    assert build_instance_sender_defaults({}).smtp is None


def test_the_defaults_carry_the_smtp_config_when_it_is_set() -> None:
    defaults = build_instance_sender_defaults(
        {"AETHERCAL_SMTP_HOST": "smtp.example.com", "AETHERCAL_SMTP_FROM": "no-reply@example.com"}
    )
    assert defaults.smtp is not None
    assert defaults.smtp.host == "smtp.example.com"


def test_the_process_edge_builds_configuration_and_never_a_sender() -> None:
    """==The regression this cut exists to prevent, asserted at the edge that used to cause it.==

    A fully-configured instance still yields no live client here. What comes back is data. The
    object that decides whose account a message leaves on is built per business, by the funnel —
    and `tests/test_sender_belt.py` walks the AST to prove nothing else builds one.
    """
    defaults = build_instance_sender_defaults(
        {
            "AETHERCAL_SMTP_HOST": "smtp.example.com",
            "AETHERCAL_SMTP_FROM": "no-reply@example.com",
            "AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.example",
            "AETHERCAL_WHATSAPP_INSTANCE": "operator",
            "AETHERCAL_WHATSAPP_API_KEY": "NOT_A_REAL_KEY",
            "AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "3",
            "AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "5",
        }
    )
    assert isinstance(defaults, InstanceSenderDefaults)
    for value in (defaults.smtp, defaults.whatsapp):
        assert not isinstance(value, SmtpEmailSender | EvolutionWhatsAppSender)


def test_a_half_configured_phone_channel_still_fails_the_boot() -> None:
    """==The one contract `build_channel_senders` existed for, kept.==

    Its recipient comes from the PUBLIC booking form, so "sending, but with no ceiling" must never
    be a state a process can reach: the only symptom would be the bill, plus a spam complaint
    against a number nobody gets back. Deleting the function must not delete this.
    """
    with pytest.raises(RuntimeError, match="half-configured"):
        build_instance_sender_defaults({"AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.example"})


def test_credentials_without_caps_still_fail_the_boot() -> None:
    """The other half of "never sending but uncapped": a full credential set, and no ceilings."""
    with pytest.raises(RuntimeError, match="DAILY_CAP"):
        build_instance_sender_defaults(
            {
                "AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.example",
                "AETHERCAL_WHATSAPP_INSTANCE": "operator",
                "AETHERCAL_WHATSAPP_API_KEY": "NOT_A_REAL_KEY",
            }
        )


# --------------------------------------------------------------------------------------
# Lifespan — attaches the shared runtime effects the booking flow reads best-effort (F1-12).
# --------------------------------------------------------------------------------------


async def test_lifespan_attaches_the_runtime_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No SMTP env: http_client + fernet_key are present, the two optional effects are None, and
    # nothing hard-fails on the missing SMTP/Google config. There is no scheduler to assert on
    # any more - it lives in `aethercal-worker` now, on its own two pools.
    for var in ("AETHERCAL_SMTP_HOST", "AETHERCAL_SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(database_url="sqlite+aiosqlite://", app_secret="test-secret")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.http_client, httpx.AsyncClient)
        assert app.state.fernet_key == settings.fernet_key()
        # ==No sender on the state at all any more (B-03bis).== This used to assert
        # `email_sender is None`, which quietly documented that the WEB process built one —
        # an object nothing in this process ever read, because the only readers are the drain's
        # ticks, and those run in `aethercal-worker`. The web reads the operator's
        # configuration and builds no client; a business's senders are resolved per item, in
        # the worker, from that item's own tenant_id.
        assert not hasattr(app.state, "email_sender")
        assert not hasattr(app.state, "channel_senders")

    # The HTTP client is closed cleanly on shutdown (no leaked resources under filterwarnings).
    assert app.state.http_client.is_closed is True


async def test_the_retired_scheduler_flag_fails_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """``AETHERCAL_RUN_SCHEDULER=1`` starts no scheduler here - ==it refuses to build Settings.==

    The flag never separated anything: the process it produced was still a full API with the admin
    mounted, and it bound all three ticks to the REQUEST PATH sessionmaker. A background tick has no
    request, therefore no ContextVar, therefore an empty GUC - and under row-level security an empty
    GUC selects **zero rows**. ``select_due`` -> 0. ``deliver_due`` -> 0. Every outbound effect
    stops, and ``/metrics`` no longer lives in this process to say so.

    Honouring it would be worse than ignoring it, and ignoring it would be worse than refusing: the
    shipped image used to SET it, so an operator upgrading would be running a web process they
    believe is draining. It is not. (Criterion 13g.)
    """
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "test-secret")
    monkeypatch.setenv("AETHERCAL_RUN_SCHEDULER", "1")

    with pytest.raises(ValueError, match="aethercal-worker"):
        create_app_from_env()


async def test_the_retired_auto_migrate_flag_fails_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AETHERCAL_AUTO_MIGRATE=1`` does not migrate here either - ==the web cannot run DDL.==

    It holds ``aethercal_app``, which owns no tables. Migrating is ``aethercal-admin db upgrade``,
    run as the OWNER - and this process then refuses to serve a schema behind head, so serving on a
    stale schema is not a state it can reach.

    Both flags survive as FIELDS purely so that they can refuse. ``extra="ignore"`` means a DELETED
    field would let the shipped image own default go on being set and silently dropped, leaving the
    operator certain that something is happening which is not. (Criterion 13.)
    """
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "test-secret")
    monkeypatch.setenv("AETHERCAL_AUTO_MIGRATE", "1")

    with pytest.raises(ValueError, match="db upgrade"):
        create_app_from_env()


async def test_the_lifespan_disposes_the_engine_when_the_boot_assertion_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused boot must not strand the engine connection pool.

    The boot assertion (``SELECT current_user``) is now the EARLIEST startup step that can fail,
    which is exactly why the engine teardown is registered on the ``AsyncExitStack`` ahead of it.
    Register it afterwards and the one startup path most likely to blow up in production leaks the
    pool.
    """
    settings = Settings(database_url="sqlite+aiosqlite://", app_secret="test-secret")

    class _FakeEngine:
        """Records disposal, so the test can prove the async engine cleanup ran."""

        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    async def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("this engine connects as the wrong role")

    monkeypatch.setattr(app_module, "build_async_engine", lambda _config: fake_engine)
    monkeypatch.setattr(app_module, "build_sessionmaker", lambda _engine: object())
    monkeypatch.setattr(app_module, "assert_engine_role", _refuse)

    app = create_app(settings)

    with pytest.raises(RuntimeError, match="wrong role"):
        async with app.router.lifespan_context(app):
            pass  # unreachable - the failure happens during startup

    assert fake_engine.disposed is True
