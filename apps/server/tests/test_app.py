"""Offline tests for the app factory: wiring, health, and the generic 401 (F1 foundation).

These use an in-memory aiosqlite URL and an ASGITransport client (which never runs the lifespan),
proving the eagerly-built engine/sessionmaker on ``app.state`` are what serve requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.app import create_app
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
