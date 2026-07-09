"""Database configuration and engine factories (no database required).

RF-19: the deployable unit is configured by environment variables, with no secrets in the source.
The config reads the URL from the environment, normalizes a bare ``postgresql://`` URL to the
psycopg 3 driver, and fails clearly when the variable is absent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from aethercal.server.db import build_async_engine, build_sync_engine
from aethercal.server.db.config import DATABASE_URL_ENV, DatabaseConfig, normalize_database_url


def test_bare_postgres_url_gets_the_psycopg_driver() -> None:
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_already_qualified_urls_are_left_alone() -> None:
    assert (
        normalize_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    )
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


def test_from_env_reads_and_normalizes_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://u:p@h/db")
    assert DatabaseConfig.from_env().url == "postgresql+psycopg://u:p@h/db"


def test_from_env_without_the_variable_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DATABASE_URL_ENV):
        DatabaseConfig.from_env()


def test_build_sync_engine_returns_a_sync_engine() -> None:
    engine = build_sync_engine(DatabaseConfig(url="postgresql+psycopg://u:p@h/db"))
    assert isinstance(engine, Engine)
    engine.dispose()


def test_build_async_engine_returns_an_async_engine() -> None:
    engine = build_async_engine(DatabaseConfig(url="postgresql+psycopg://u:p@h/db"))
    assert isinstance(engine, AsyncEngine)
    engine.sync_engine.dispose()
