"""Copying ``.env.example`` verbatim must not produce a bootable server. ==The operator's act.==

The quickstart's step 1 is ``cp .env.example .env``. Everything after it assumes the operator edited
the placeholders — and nothing in the product ever checked. An instance that skipped that edit ran
perfectly on ``AETHERCAL_APP_SECRET=CHANGE_ME_LONG_RANDOM_SECRET``, which is a string printed in a
public repository.

==That is not a weak key. It is a PUBLISHED one, and it is the key that decrypts money.== The Fernet
key is a pure function of the app secret, so anybody with a clone can derive it and read every
business's Stripe and Mercado Pago credentials out of a database dump. The encryption's whole stated
value — "a stolen dump is useless without the app secret" — is void the moment the app secret is one
the repository hands out.

.. rubric:: The test is the OPERATOR'S action, not a field's validator

It builds ``Settings`` from the environment ``.env.example`` actually produces, because that is the
thing that must fail. Asserting that a validator rejects one string would prove that a validator
rejects one string; it would not prove that copying the shipped file lands anywhere near it.

.. rubric:: ==``previous_app_secret`` is deliberately NOT guarded, and that is not an oversight==

It is the RETIRING secret during a key rotation, and an instance that has been running on the
placeholder must set it to *the placeholder* in order to rotate OFF it. Guarding it would refuse the
one command that escapes this bug — a guard that locks the fire exit and calls it security.
:func:`test_the_rotation_away_from_a_published_secret_is_not_blocked` holds that door open.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from aethercal.core.placeholders import PublishedPlaceholderError
from aethercal.server.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / "deploy" / ".env.example"

REAL_SECRET = "eHF2bXo0N3RwbHc5c2RrZ2ExYnVqeTNucjZ4YzhmaDA"
ANOTHER_REAL_SECRET = "d3o5bWt4MnJoNGx0YjhqcXZuNmM3ZzFzeTBmcGEyZQ"
PLAIN_URL = "postgresql://aethercal_app:pw@postgres:5432/aethercal"

_ASSIGNMENT = re.compile(r"^(?P<var>AETHERCAL_[A-Z0-9_]*)=(?P<value>.*)$", re.MULTILINE)


def _env_example_as_the_operator_gets_it() -> dict[str, str]:
    """The ``AETHERCAL_*`` environment a verbatim ``cp .env.example .env`` produces. ==Read.=="""
    assert ENV_EXAMPLE.is_file(), f"the shipped example env file is not at {ENV_EXAMPLE}"
    found = {
        match.group("var"): match.group("value")
        for match in _ASSIGNMENT.finditer(ENV_EXAMPLE.read_text(encoding="utf-8"))
    }
    assert found, "parsed no AETHERCAL_* variables — this test would pass by measuring nothing"
    return found


def test_the_shipped_example_env_publishes_the_app_secret() -> None:
    """The premise, measured rather than assumed: the file really does hand out a placeholder."""
    assert "CHANGE_ME" in _env_example_as_the_operator_gets_it()["AETHERCAL_APP_SECRET"]


def test_a_verbatim_copy_of_the_example_env_does_not_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The whole finding, as the operator performs it:== ``cp .env.example .env`` and start.

    Every ``AETHERCAL_*`` the shipped file defines, exactly as it defines it, read the way
    ``Settings`` reads it in production. It must refuse.

    The type is pydantic's ``ValidationError``, not the guard's own class: pydantic wraps every
    ``ValueError`` a field validator raises, exactly as it does for the other eight validators on
    this model. That is the contract the boot actually has — so it is what this asserts, along with
    the message surviving the wrapping, because a refusal nobody can read is a refusal that gets
    worked around.
    """
    for key, value in _env_example_as_the_operator_gets_it().items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError) as raised:
        Settings()  # type: ignore[call-arg]  # sourced from the environment, exactly as in prod

    assert "AETHERCAL_APP_SECRET" in str(raised.value)
    assert "PUBLISHED" in str(raised.value)


def test_the_guard_is_what_refuses_it_and_not_some_other_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The control for the test above.== A model with nine validators refuses a lot of things.

    Without this, the sweep would go green if the example env tripped some UNRELATED rule — a bad
    CIDR, a retired tripwire — and the placeholder would sail through while the test claimed
    otherwise. So this asserts the refusal is THIS one, by its own exception type, on its own field.
    """
    for key, value in _env_example_as_the_operator_gets_it().items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError) as raised:
        Settings()  # type: ignore[call-arg]

    causes = [error.get("ctx", {}).get("error") for error in raised.value.errors()]
    assert any(isinstance(cause, PublishedPlaceholderError) for cause in causes), (
        f"the example env was refused, but not by the published-placeholder guard: {causes}"
    )


def test_an_edited_env_boots() -> None:
    """==The anti-vacuity half.== The operator who DID the edit must get a running instance.

    Without this, a guard that refused every configuration on earth would pass the test above.
    """
    settings = Settings(database_url=PLAIN_URL, app_secret=REAL_SECRET)  # type: ignore[call-arg]

    assert settings.app_secret == REAL_SECRET
    assert settings.fernet_key()


def test_the_rotation_away_from_a_published_secret_is_not_blocked() -> None:
    """==The fire exit, held open on purpose.==

    An instance that has been running on the published placeholder needs
    ``previous_app_secret=CHANGE_ME_LONG_RANDOM_SECRET`` so ``credentials rotate-key`` can decrypt
    the rows it must move onto the new key. Guarding this field would refuse the only command that
    escapes the bug.
    """
    settings = Settings(  # type: ignore[call-arg]
        database_url=PLAIN_URL,
        app_secret=ANOTHER_REAL_SECRET,
        previous_app_secret="CHANGE_ME_LONG_RANDOM_SECRET",
    )

    assert len(settings.decryption_fernet_keys()) == 2, (
        "the retiring published secret must still decrypt, or every row encrypted under it is lost"
    )
