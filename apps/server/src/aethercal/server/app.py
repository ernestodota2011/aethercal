"""The FastAPI application factory (F1 foundation).

``create_app`` builds the async engine and ``async_sessionmaker`` **eagerly** and stores them on
``app.state`` so requests work under an ASGITransport test client, which never runs the lifespan.
The lifespan only does the things that must happen against a live process: run the boot migrator
(when ``auto_migrate`` is set) on startup, and dispose the async engine on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from aethercal.schemas import ErrorResponse
from aethercal.server.api import api_router
from aethercal.server.api.auth import AuthenticationError
from aethercal.server.db.engine import build_async_engine, build_sessionmaker, build_sync_engine
from aethercal.server.db.migrate import run_migrations
from aethercal.server.settings import Settings


async def _handle_authentication_error(request: Request, exc: Exception) -> JSONResponse:
    """Map any :class:`AuthenticationError` to a generic 401 (RF-16: never disclose why)."""
    payload = ErrorResponse(error="unauthorized", message="Invalid or missing API key")
    return JSONResponse(
        status_code=401,
        content=payload.model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_app(settings: Settings) -> FastAPI:
    """Build the AetherCal API application from ``settings``."""
    engine = build_async_engine(settings.database_config())
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if settings.auto_migrate:
            # The boot migrator is synchronous (Alembic); run it on a throwaway sync engine.
            sync_engine = build_sync_engine(settings.database_config())
            try:
                run_migrations(sync_engine)
            finally:
                sync_engine.dispose()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    app.include_router(api_router)
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)

    return app
