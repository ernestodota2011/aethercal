"""The admin authentication decision (F1-11, RF-18).

One pure function, :func:`authenticate`, backs the login handler: the presented username must match
the configured one and the presented password must verify against the configured PBKDF2 hash. Both
comparisons always run — the username check does not short-circuit the (deliberately slow) password
check — so the boolean result does not leak which half failed, and username comparison is
constant-time (:func:`hmac.compare_digest`).
"""

from __future__ import annotations

import hmac

from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.passwords import verify_password


def authenticate(config: AdminConfig, username: str, password: str) -> bool:
    """Return whether ``(username, password)`` are the configured operator's credentials.

    Evaluates both factors unconditionally (no early return) so a wrong username and a wrong
    password are indistinguishable in timing and outcome.
    """
    username_ok = hmac.compare_digest(username.encode("utf-8"), config.username.encode("utf-8"))
    password_ok = verify_password(config.password_hash, password)
    return username_ok and password_ok


__all__ = ["authenticate"]
