"""SMTP configuration, sourced from the environment (RF-08 / RF-19: no secrets in the source).

Modeled on :class:`~aethercal.server.db.config.DatabaseConfig`: a frozen dataclass whose
``from_env`` classmethod reads ``AETHERCAL_SMTP_*`` variables from an explicit ``environ`` mapping
(defaulting to :data:`os.environ`). ``host`` and ``from_addr`` are required; ``port`` defaults to
the submission port 587, ``use_tls`` to ``True``, and ``username`` / ``password`` are optional (open
relay needs no auth). The password only ever lives in the process environment — never in source.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_HOST_ENV = "AETHERCAL_SMTP_HOST"
_PORT_ENV = "AETHERCAL_SMTP_PORT"
_USERNAME_ENV = "AETHERCAL_SMTP_USERNAME"
_PASSWORD_ENV = "AETHERCAL_SMTP_PASSWORD"
_FROM_ENV = "AETHERCAL_SMTP_FROM"
_USE_TLS_ENV = "AETHERCAL_SMTP_USE_TLS"

_DEFAULT_PORT = 587
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class SmtpConfig:
    """Everything needed to talk to an SMTP relay. ``from_addr`` is the default ``From`` header."""

    host: str
    from_addr: str
    port: int = _DEFAULT_PORT
    username: str | None = None
    password: str | None = None
    use_tls: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SmtpConfig:
        """Build an :class:`SmtpConfig` from ``AETHERCAL_SMTP_*`` variables (RF-19).

        Raises :class:`RuntimeError` with a clear, variable-named message when a required value is
        missing or ``AETHERCAL_SMTP_PORT`` is not an integer.
        """
        env = os.environ if environ is None else environ
        host = _require(env, _HOST_ENV)
        from_addr = _require(env, _FROM_ENV)
        return cls(
            host=host,
            from_addr=from_addr,
            port=_parse_port(env.get(_PORT_ENV)),
            username=env.get(_USERNAME_ENV) or None,
            password=env.get(_PASSWORD_ENV) or None,
            use_tls=_parse_bool(env.get(_USE_TLS_ENV), default=True),
        )


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise RuntimeError(
            f"{key} is not set; SMTP must be configured via the environment "
            "(RF-19: no secrets in source)."
        )
    return value


def _parse_port(raw: str | None) -> int:
    if raw is None or raw == "":
        return _DEFAULT_PORT
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{_PORT_ENV} must be an integer, got {raw!r}.") from exc


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise RuntimeError(f"{_USE_TLS_ENV} must be a boolean, got {raw!r}.")


__all__ = ["SmtpConfig"]
