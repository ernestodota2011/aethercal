"""Tests for the env-sourced server Settings (RF-19)."""

from __future__ import annotations

import pytest

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import DatabaseConfig
from aethercal.server.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://user:pw@db.example:5432/aethercal",
        "app_secret": "unit-test-secret",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_defaults_are_sensible() -> None:
    settings = _settings()
    assert settings.auto_migrate is True
    assert settings.echo_sql is False
    # The scheduler is OFF by default so the offline test/API path never starts a background loop;
    # the container turns it on with AETHERCAL_RUN_SCHEDULER=1 in exactly one process (RF-19).
    assert settings.run_scheduler is False
    # No public booking base by default → the request path falls back to the request's base URL.
    assert settings.booking_base_url is None


def test_run_scheduler_reads_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_RUN_SCHEDULER", "true")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.run_scheduler is True


def test_booking_base_url_reads_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_BOOKING_BASE_URL", "https://book.example.com")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.booking_base_url == "https://book.example.com"


def test_database_config_normalizes_the_url_to_psycopg() -> None:
    config = _settings().database_config()
    assert isinstance(config, DatabaseConfig)
    assert config.url == "postgresql+psycopg://user:pw@db.example:5432/aethercal"


def test_echo_sql_flows_into_the_database_config() -> None:
    config = _settings(echo_sql=True).database_config()
    assert config.echo is True


def test_qualified_driver_url_is_left_untouched() -> None:
    config = _settings(database_url="sqlite+aiosqlite:///./local.db").database_config()
    assert config.url == "sqlite+aiosqlite:///./local.db"


def test_fernet_key_is_derived_from_the_app_secret() -> None:
    settings = _settings(app_secret="derive-me")
    assert settings.fernet_key() == derive_fernet_key("derive-me")


def test_settings_read_from_the_environment_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_AUTO_MIGRATE", "false")
    # Constructed with no explicit args → every field comes from the environment.
    settings = Settings()  # type: ignore[call-arg]
    assert settings.app_secret == "from-env"
    assert settings.auto_migrate is False
    assert settings.database_config().url == "postgresql+psycopg://u:p@h/db"
