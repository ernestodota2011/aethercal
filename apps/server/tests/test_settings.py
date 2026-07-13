"""Tests for the env-sourced server Settings (RF-19)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import DatabaseConfig
from aethercal.server.scheduler import (
    DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS,
    DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS,
    DEFAULT_WEBHOOK_INTERVAL_SECONDS,
)
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
    assert settings.echo_sql is False
    # Both of these are RETIRED, and both survive only as tripwires: a truthy value now FAILS the
    # boot. Off is therefore the default, and the only value either may hold. Migrations are
    # `aethercal-admin db upgrade` (as the OWNER); the drain is the `aethercal-worker` process.
    assert settings.auto_migrate is False
    assert settings.run_scheduler is False
    # No public booking base by default → the request path falls back to the request's base URL.
    assert settings.booking_base_url is None


def test_run_scheduler_is_retired_and_a_truthy_value_fails_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==It is not read from the environment any more. It REFUSES to be.==

    The field survives so that it can refuse. ``extra="ignore"`` means deleting it would let the
    shipped image own ``AETHERCAL_RUN_SCHEDULER=1`` go on being set, be silently dropped, and
    leave an operator certain that a drain is running which is not. The drain lives in the
    ``aethercal-worker`` process now, on its own two pools.
    """
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_RUN_SCHEDULER", "true")

    with pytest.raises(ValueError, match="aethercal-worker"):
        Settings()  # type: ignore[call-arg]


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


# --------------------------------------------------------------------------------------
# Scheduler tick intervals (RF-19): production defaults, overridable from the environment.
# --------------------------------------------------------------------------------------
def test_scheduler_intervals_default_to_the_production_values() -> None:
    settings = _settings()
    assert settings.webhook_interval_seconds == DEFAULT_WEBHOOK_INTERVAL_SECONDS
    assert settings.busy_refresh_interval_seconds == DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS
    assert settings.outbox_drain_interval_seconds == DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS


def test_scheduler_intervals_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_WEBHOOK_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AETHERCAL_OUTBOX_DRAIN_INTERVAL_SECONDS", "5")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.webhook_interval_seconds == 5
    assert settings.outbox_drain_interval_seconds == 5
    # Untouched knobs keep their production value — an override is not a reset.
    assert settings.busy_refresh_interval_seconds == DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS


@pytest.mark.parametrize("value", [0, -1])
def test_a_non_positive_interval_is_refused(value: int) -> None:
    # An interval of 0 would make APScheduler spin (or refuse the job) — fail at the edge, loudly,
    # rather than boot a scheduler that ticks wrong or not at all.
    with pytest.raises(ValidationError):
        _settings(outbox_drain_interval_seconds=value)


# --------------------------------------------------------------------------------------
# The private-target allowlist: read from the ENVIRONMENT, fail-closed, fail-loud at BOOT.
# --------------------------------------------------------------------------------------


def test_the_private_target_allowlist_is_empty_by_default() -> None:
    """==Fail-closed.== Upgrading to a build that CAN reach private networks must not reach any."""
    settings = _settings()
    assert settings.webhook_private_target_cidrs == ""
    assert settings.private_target_allowlist().is_empty
    assert settings.private_target_allowlist().permits("192.168.1.50") is False


def test_the_private_target_allowlist_reads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The ONE property the whole feature rests on: the operator declares it, and nobody else.==

    A webhook URL is caller-supplied; the networks it may reach come from the process environment.
    An attacker who controls a subscription URL cannot widen this list, because there is no code
    path from a request, a row or an API response into it.
    """
    monkeypatch.setenv("AETHERCAL_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("AETHERCAL_APP_SECRET", "from-env")
    monkeypatch.setenv("AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS", "192.168.1.0/24,172.17.0.0/16")

    settings = Settings()  # type: ignore[call-arg]
    allowlist = settings.private_target_allowlist()

    assert allowlist.permits("192.168.1.50") is True  # the operator's n8n
    assert allowlist.permits("172.17.0.2") is True  # the Docker bridge
    assert allowlist.permits("10.0.0.1") is False  # never declared
    assert allowlist.permits("169.254.169.254") is False  # cloud metadata, and undeclarable


@pytest.mark.parametrize(
    "value",
    [
        "0.0.0.0/0",  # the default route — would allowlist loopback and metadata in one token
        "169.254.0.0/16",  # link-local — the cloud metadata endpoint
        "8.8.8.0/24",  # public space
        "10.0.0.0",  # no prefix — silently means /32, i.e. "nothing"
        "192.168.1.5/24",  # host bits set — a typo for 192.168.1.0/24
        "not-a-cidr",
    ],
)
def test_a_misconfigured_allowlist_fails_the_BOOT(value: str) -> None:
    """==A misconfiguration must not come up quietly permitting nothing.==

    That is the failure mode this project is named after: the operator declares their LAN, the app
    starts, and every delivery keeps dying exactly as before — now with a "configured" allowlist to
    prove it should have worked. Pydantic turns the AllowlistConfigError into a ValidationError, so
    the process refuses to start and says which token it choked on.
    """
    with pytest.raises(ValidationError):
        _settings(webhook_private_target_cidrs=value)
