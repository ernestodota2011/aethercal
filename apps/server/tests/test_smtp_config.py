"""``SmtpConfig.from_env`` tests (RF-08 / RF-19): SMTP config is env-sourced, no secrets in source.

The config mirrors :class:`~aethercal.server.db.config.DatabaseConfig` — a frozen dataclass with a
``from_env`` classmethod that takes an explicit ``environ`` mapping (so the offline suite never
reads process environment). ``host`` and ``from_addr`` are required; a missing one raises an error.
"""

from __future__ import annotations

import dataclasses

import pytest

from aethercal.server.integrations.smtp.config import SmtpConfig

_FULL_ENV = {
    "AETHERCAL_SMTP_HOST": "smtp.example.com",
    "AETHERCAL_SMTP_PORT": "2525",
    "AETHERCAL_SMTP_USERNAME": "mailer",
    "AETHERCAL_SMTP_PASSWORD": "s3cret",
    "AETHERCAL_SMTP_FROM": "AetherCal <no-reply@example.com>",
    "AETHERCAL_SMTP_USE_TLS": "false",
}


def test_from_env_reads_every_field() -> None:
    cfg = SmtpConfig.from_env(_FULL_ENV)
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 2525
    assert cfg.username == "mailer"
    assert cfg.password == "s3cret"
    assert cfg.from_addr == "AetherCal <no-reply@example.com>"
    assert cfg.use_tls is False


def test_from_env_applies_defaults_for_optional_fields() -> None:
    cfg = SmtpConfig.from_env(
        {
            "AETHERCAL_SMTP_HOST": "smtp.example.com",
            "AETHERCAL_SMTP_FROM": "no-reply@example.com",
        }
    )
    assert cfg.port == 587
    assert cfg.use_tls is True
    assert cfg.username is None
    assert cfg.password is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("NO", False)],
)
def test_from_env_parses_use_tls_booleans(raw: str, expected: bool) -> None:
    cfg = SmtpConfig.from_env(
        {
            "AETHERCAL_SMTP_HOST": "smtp.example.com",
            "AETHERCAL_SMTP_FROM": "no-reply@example.com",
            "AETHERCAL_SMTP_USE_TLS": raw,
        }
    )
    assert cfg.use_tls is expected


def test_from_env_missing_host_raises_clear_error() -> None:
    with pytest.raises(RuntimeError, match="AETHERCAL_SMTP_HOST"):
        SmtpConfig.from_env({"AETHERCAL_SMTP_FROM": "no-reply@example.com"})


def test_from_env_missing_from_raises_clear_error() -> None:
    with pytest.raises(RuntimeError, match="AETHERCAL_SMTP_FROM"):
        SmtpConfig.from_env({"AETHERCAL_SMTP_HOST": "smtp.example.com"})


def test_from_env_non_integer_port_raises_clear_error() -> None:
    with pytest.raises(RuntimeError, match="AETHERCAL_SMTP_PORT"):
        SmtpConfig.from_env(
            {
                "AETHERCAL_SMTP_HOST": "smtp.example.com",
                "AETHERCAL_SMTP_FROM": "no-reply@example.com",
                "AETHERCAL_SMTP_PORT": "not-a-number",
            }
        )


def test_config_is_frozen() -> None:
    cfg = SmtpConfig.from_env(_FULL_ENV)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.host = "evil.example.com"  # type: ignore[misc]
