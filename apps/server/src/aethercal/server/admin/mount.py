"""Mount the Reflex admin into the server app factory (F1-11, RF-18).

This is the one additive seam the server's ``create_app`` calls. It is *off by default* and
gated twice (credentials configured AND ``AETHERCAL_ADMIN_ENABLED`` truthy), so the plain API server
— and the offline ``poe check`` path — never stands up a frontend it cannot build. The module keeps
Reflex out of its import graph on purpose (only :mod:`.config` is imported at top): reflex is pulled
in lazily inside the live mount, so importing ``server.app`` stays cheap when the admin is disabled.

The mounted admin reuses the server's *own* ``app.state.sessionmaker`` — one engine/pool for the
process, disposed by the existing lifespan — so the admin's Reflex backend truly shares the
in-process DB layer rather than opening a second, separately-managed pool.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from aethercal.server.admin.config import AdminConfig, admin_mount_enabled

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ADMIN_MOUNT_PATH = "/admin"


def mount_admin(
    app: FastAPI,
    environ: Mapping[str, str] | None = None,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """Mount the Reflex admin under ``/admin`` when configured + enabled; return whether it mounted.

    A no-op (returns ``False``) unless both ``AETHERCAL_ADMIN_USERNAME`` / ``_PASSWORD_HASH`` are
    set and ``AETHERCAL_ADMIN_ENABLED`` is truthy — the default server is unchanged.

    ``sessionmaker`` is the same-process DB session factory the admin's Reflex backend uses. The
    caller passes it explicitly (``create_app`` builds engine + sessionmaker eagerly and hands the
    factory straight in), so the mount never reaches into ``app.state`` for a dependency whose
    lifecycle is the app's — it is an explicit input. Falls back to ``app.state.sessionmaker`` only
    when a caller omits it.
    """
    env = os.environ if environ is None else environ
    config = AdminConfig.from_env(env)
    if config is None or not admin_mount_enabled(env):
        return False
    session_factory = sessionmaker if sessionmaker is not None else app.state.sessionmaker
    _mount_admin_app(app, config, session_factory)
    return True


def _mount_admin_app(
    app: FastAPI,
    config: AdminConfig,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:  # pragma: no cover - live (needs the built frontend + a running server)
    """Build the Reflex ASGI admin over the given sessionmaker and mount it at ``/admin``."""
    # Imported lazily on purpose: keeps reflex out of the import graph when the admin is disabled.
    from aethercal.server.admin.app import build_admin_asgi  # noqa: PLC0415
    from aethercal.server.admin.runtime import AdminRuntime  # noqa: PLC0415

    runtime = AdminRuntime(sessionmaker=sessionmaker, config=config)
    app.mount(ADMIN_MOUNT_PATH, build_admin_asgi(runtime))


__all__ = ["ADMIN_MOUNT_PATH", "mount_admin"]
