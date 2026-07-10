"""Tests for deriving a Fernet key from the app secret."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from aethercal.server.crypto import derive_fernet_key


def test_derived_key_is_a_usable_fernet_key() -> None:
    key = derive_fernet_key("super-secret-app-key")
    fernet = Fernet(key)
    token = fernet.encrypt(b"provider credentials")
    assert fernet.decrypt(token) == b"provider credentials"


def test_derivation_is_deterministic() -> None:
    assert derive_fernet_key("same-secret") == derive_fernet_key("same-secret")


def test_different_secrets_derive_different_keys() -> None:
    assert derive_fernet_key("secret-a") != derive_fernet_key("secret-b")


def test_empty_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="app_secret"):
        derive_fernet_key("")
