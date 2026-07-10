"""Unit tests for the admin env config + mount gate (F1-11, RF-18/RF-19).

The admin is *off by default*: it only wires up when the operator has configured credentials, and it
only mounts when explicitly enabled. Both gates read the environment (never the source), mirroring
``SmtpConfig.from_env`` — an absent config degrades to ``None`` rather than hard-failing boot.
"""

from __future__ import annotations

from aethercal.server.admin.config import AdminConfig, admin_mount_enabled

_USER = "AETHERCAL_ADMIN_USERNAME"
_HASH = "AETHERCAL_ADMIN_PASSWORD_HASH"
_SLUG = "AETHERCAL_ADMIN_TENANT_SLUG"
_ENABLED = "AETHERCAL_ADMIN_ENABLED"


def test_from_env_is_none_when_unconfigured() -> None:
    assert AdminConfig.from_env({}) is None


def test_from_env_is_none_when_only_username_is_set() -> None:
    assert AdminConfig.from_env({_USER: "admin"}) is None


def test_from_env_is_none_when_only_hash_is_set() -> None:
    assert AdminConfig.from_env({_HASH: "pbkdf2_sha256$1$aa$bb"}) is None


def test_from_env_builds_a_config_from_username_and_hash() -> None:
    config = AdminConfig.from_env({_USER: "admin", _HASH: "pbkdf2_sha256$1$aa$bb"})
    assert config is not None
    assert config.username == "admin"
    assert config.password_hash == "pbkdf2_sha256$1$aa$bb"
    assert config.tenant_slug is None  # optional, absent


def test_from_env_reads_the_optional_tenant_slug() -> None:
    config = AdminConfig.from_env({_USER: "admin", _HASH: "pbkdf2_sha256$1$aa$bb", _SLUG: "acme"})
    assert config is not None
    assert config.tenant_slug == "acme"


def test_blank_values_are_treated_as_unset() -> None:
    assert AdminConfig.from_env({_USER: "  ", _HASH: "pbkdf2_sha256$1$aa$bb"}) is None
    config = AdminConfig.from_env({_USER: "admin", _HASH: "pbkdf2_sha256$1$aa$bb", _SLUG: "   "})
    assert config is not None
    assert config.tenant_slug is None


def test_mount_is_disabled_by_default() -> None:
    assert admin_mount_enabled({}) is False
    assert admin_mount_enabled({_ENABLED: "0"}) is False
    assert admin_mount_enabled({_ENABLED: "false"}) is False


def test_mount_enables_on_a_truthy_flag() -> None:
    assert admin_mount_enabled({_ENABLED: "1"}) is True
    assert admin_mount_enabled({_ENABLED: "true"}) is True
    assert admin_mount_enabled({_ENABLED: "ON"}) is True
