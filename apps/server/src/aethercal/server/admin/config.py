"""Admin configuration, sourced from the environment (F1-11, RF-18/RF-19).

Two independent, off-by-default gates, both read from the environment (never the source):

* :meth:`AdminConfig.from_env` returns the operator's credentials, or ``None`` when they are not
  configured — so the admin has no login until ``AETHERCAL_ADMIN_USERNAME`` and
  ``AETHERCAL_ADMIN_PASSWORD_HASH`` are both set. It degrades to ``None`` rather than raising
  (mirroring :func:`build_email_sender`), so an unconfigured admin never blocks boot.
* :func:`admin_mount_enabled` gates the *mounting* of the (heavier) Reflex sub-app behind an
  explicit ``AETHERCAL_ADMIN_ENABLED`` flag, so the default server — and the offline test/`poe
  check` path — never stands up a frontend it cannot build without Node.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_USERNAME_ENV = "AETHERCAL_ADMIN_USERNAME"
_PASSWORD_HASH_ENV = "AETHERCAL_ADMIN_PASSWORD_HASH"
_TENANT_SLUG_ENV = "AETHERCAL_ADMIN_TENANT_SLUG"
_ENABLED_ENV = "AETHERCAL_ADMIN_ENABLED"

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class AdminConfig:
    """The single operator's credentials and (optionally) the tenant slug they administer."""

    username: str
    password_hash: str
    tenant_slug: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AdminConfig | None:
        """Build the admin config from the environment, or ``None`` when it is not configured.

        Both ``AETHERCAL_ADMIN_USERNAME`` and ``AETHERCAL_ADMIN_PASSWORD_HASH`` must be present and
        non-blank; ``AETHERCAL_ADMIN_TENANT_SLUG`` is optional (``None`` selects the single tenant).
        """
        env = os.environ if environ is None else environ
        username = _clean(env.get(_USERNAME_ENV))
        password_hash = _clean(env.get(_PASSWORD_HASH_ENV))
        if username is None or password_hash is None:
            return None
        return cls(
            username=username,
            password_hash=password_hash,
            tenant_slug=_clean(env.get(_TENANT_SLUG_ENV)),
        )


def admin_mount_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the Reflex admin should be mounted (``AETHERCAL_ADMIN_ENABLED`` is truthy)."""
    env = os.environ if environ is None else environ
    raw = env.get(_ENABLED_ENV)
    return raw is not None and raw.strip().lower() in _TRUE_TOKENS


def _clean(raw: str | None) -> str | None:
    """Strip ``raw`` and treat empty/blank as unset (``None``)."""
    if raw is None:
        return None
    value = raw.strip()
    return value or None


__all__ = ["AdminConfig", "admin_mount_enabled"]
