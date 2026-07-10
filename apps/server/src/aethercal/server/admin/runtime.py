"""The admin runtime holder (F1-11).

Reflex state handlers are instance methods with no place to inject dependencies, so the admin's
same-process ``async_sessionmaker`` and its :class:`AdminConfig` are stashed in a process-global
holder that :func:`build_admin_app` configures once at build time and the state reads at request
time. It is a single-process, single-operator admin, so one configured runtime is exactly right;
the holder is a small class attribute (not a ``global`` statement) so it stays lint-clean.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.admin.config import AdminConfig


@dataclass(frozen=True, slots=True)
class AdminRuntime:
    """Everything the admin state needs to serve a request: the DB session factory + the config."""

    sessionmaker: async_sessionmaker[AsyncSession]
    config: AdminConfig


class _Holder:
    """A tiny mutable box for the process-global runtime (avoids a ``global`` statement)."""

    value: AdminRuntime | None = None


def configure_runtime(runtime: AdminRuntime) -> None:
    """Install the process-global admin runtime (called once by :func:`build_admin_app`)."""
    _Holder.value = runtime


def current_runtime() -> AdminRuntime:
    """Return the configured runtime, or raise if the admin was not built before use."""
    if _Holder.value is None:
        raise RuntimeError("admin runtime is not configured; build the admin app first")
    return _Holder.value


__all__ = ["AdminRuntime", "configure_runtime", "current_runtime"]
