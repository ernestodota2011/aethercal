"""Unit tests for the admin authentication decision (F1-11, RF-18).

``authenticate`` is the single yes/no gate the login handler calls: the presented username must
match the configured one AND the presented password must verify against the configured hash. Both
checks always run (no early-out on a wrong username) so the result never leaks which half failed.
"""

from __future__ import annotations

from aethercal.server.admin.auth import authenticate
from aethercal.server.admin.config import AdminConfig
from aethercal.server.admin.passwords import hash_password


def _config(username: str = "operator", password: str = "hunter2") -> AdminConfig:
    return AdminConfig(username=username, password_hash=hash_password(password), tenant_slug=None)


def test_correct_username_and_password_authenticates() -> None:
    assert authenticate(_config(), "operator", "hunter2") is True


def test_wrong_password_is_rejected() -> None:
    assert authenticate(_config(), "operator", "wrong") is False


def test_wrong_username_is_rejected() -> None:
    assert authenticate(_config(), "intruder", "hunter2") is False


def test_both_wrong_is_rejected() -> None:
    assert authenticate(_config(), "intruder", "wrong") is False


def test_empty_credentials_are_rejected() -> None:
    assert authenticate(_config(), "", "") is False


def test_a_broken_stored_hash_never_authenticates() -> None:
    broken = AdminConfig(username="operator", password_hash="not-a-hash", tenant_slug=None)
    assert authenticate(broken, "operator", "hunter2") is False
