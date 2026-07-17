"""Tests for deriving a Fernet key from the app secret, and reading under a rotation window."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from aethercal.server.crypto import decrypt_secret, derive_fernet_key, encrypt_secret


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


# ==========================================================================================
# The read WINDOW (B-03): decrypt accepts the CURRENT key, and — while a rotation is in
# flight — the PREVIOUS one too. A process restarted with both can open a row written under
# EITHER, so it never has to write under the key about to be retired just to stay readable.
# ==========================================================================================


def test_decrypt_secret_round_trips_with_a_single_current_key() -> None:
    """The steady state: one key, and it opens what it wrote. Unchanged behaviour."""
    key = derive_fernet_key("current")
    assert decrypt_secret(encrypt_secret(b"stripe-secret", key), key) == b"stripe-secret"


def test_decrypt_secret_reads_a_current_key_token_when_the_previous_is_also_offered() -> None:
    """Offering the previous key must not disturb the normal case: a row on the CURRENT key
    keeps reading, so a mid-rotation process is never worse at reading than a steady one."""
    current, previous = derive_fernet_key("new"), derive_fernet_key("old")
    token = encrypt_secret(b"stripe-secret", current)
    assert decrypt_secret(token, [current, previous]) == b"stripe-secret"


def test_decrypt_secret_reads_a_previous_key_token_only_when_the_previous_key_is_offered() -> None:
    """==The heart of the fix.== A row still on the RETIRING key — one the rotation has not
    reached — opens under ``[current, previous]``, and does NOT under the current key alone."""
    current, previous = derive_fernet_key("new"), derive_fernet_key("old")
    token = encrypt_secret(b"webhook-secret", previous)

    assert decrypt_secret(token, [current, previous]) == b"webhook-secret"

    # A reader holding only the new secret cannot open a row still on the old one — which is
    # exactly the window that forced a write under the retiring key, the bug this closes.
    with pytest.raises(InvalidToken):
        decrypt_secret(token, current)
    with pytest.raises(InvalidToken):
        decrypt_secret(token, [current])


def test_decrypt_secret_raises_when_no_key_opens_the_token() -> None:
    """A token under NEITHER offered key is refused — a lost row must be loud, never silent."""
    current, previous = derive_fernet_key("new"), derive_fernet_key("old")
    stranger = encrypt_secret(b"x", derive_fernet_key("a-secret-this-instance-never-had"))
    with pytest.raises(InvalidToken):
        decrypt_secret(stranger, [current, previous])
