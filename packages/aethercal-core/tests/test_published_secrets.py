"""A value the repository publishes is not a secret — the one place that answers, and its rule.

.. rubric:: The finding: the fix for a published key, wearing different clothes

The booking page used to let FastHTML mint its cookie-signing key into a ``.sesskey`` file, which
was committed — so production signed cookies with a key anybody could read on GitHub. That was
closed by making the key a required environment variable.

==And it reopened immediately, one step to the left.== ``deploy/.env.example`` ships
``AETHERCAL_BOOKING_SECRET=CHANGE_ME_LONG_RANDOM_BOOKING_SECRET``, the quickstart's step 1 is
``cp .env.example .env``, and any non-blank string passed every check there was. The outcome is
identical — an instance signing with a **publicly known** secret — and it arrives the way it always
does: ==by omission==. Nobody chooses to publish their key. They just do not edit the placeholder.

.. rubric:: The rule is "this value is PUBLIC", not "this value is weak"

No entropy heuristics, no length minimums, no dictionary checks. The question this module answers is
narrow and factual: ==is this the value the repository itself hands the world?== The repository
already marks those, in its own convention, with ``CHANGE_ME``. So the rule reads that marker rather
than guessing at strength — a guard that tried to judge "strong enough" would reject real secrets,
be switched off, and take this one with it.

.. rubric:: ==Why ONE site, and why the test derives from the file rather than a list==

``AETHERCAL_APP_SECRET`` had exactly the same hole, and it is worse: it derives the Fernet key that
encrypts every business's payment credentials, so the published placeholder yields a key anybody can
reproduce from a clone. Two processes, two settings classes, one question — and the answer must not
be written twice, because two copies drift and neither goes red when they do.

:meth:`TestTheRuleIsDerivedFromTheFile.test_every_placeholder_the_repository_publishes_is_refused`
is the load-bearing test: it PARSES ``deploy/.env.example`` and requires the guard to refuse every
placeholder it finds. Nothing is enumerated here, so a tenth placeholder added tomorrow is covered
on the day it is added — and if the marker convention is ever changed, the parser finds nothing and
the ``assert`` turns the whole thing red rather than passing by measuring an empty set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aethercal.core.placeholders import (
    PLACEHOLDER_MARKER,
    PublishedPlaceholderError,
    assert_not_published_placeholder,
    is_published_placeholder,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / "deploy" / ".env.example"

# A real secret, of the shape the guard's own message tells operators to generate.
REAL_SECRET = "eHF2bXo0N3RwbHc5c2RrZ2ExYnVqeTNucjZ4YzhmaDA"

_ASSIGNMENT = re.compile(r"^(?P<var>[A-Z][A-Z0-9_]*)=(?P<value>.*)$", re.MULTILINE)


def published_values() -> dict[str, str]:
    """Every ``VAR=value`` the shipped example file hands an operator. ==Read, never listed.=="""
    assert ENV_EXAMPLE.is_file(), f"the shipped example env file is not at {ENV_EXAMPLE}"
    return {
        match.group("var"): match.group("value")
        for match in _ASSIGNMENT.finditer(ENV_EXAMPLE.read_text(encoding="utf-8"))
    }


def published_placeholders() -> dict[str, str]:
    """The subset an operator is expected to REPLACE — the repo's own ``CHANGE_ME`` convention."""
    return {var: value for var, value in published_values().items() if PLACEHOLDER_MARKER in value}


class TestTheRuleIsDerivedFromTheFile:
    def test_every_placeholder_the_repository_publishes_is_refused(self) -> None:
        """==The one that matters, and it enumerates nothing.==

        Parse the file the quickstart tells operators to copy, take every value it asks them to
        change, and require the guard to refuse each. A tenth placeholder added tomorrow is covered
        the day it lands.
        """
        placeholders = published_placeholders()

        assert placeholders, (
            "no placeholders found in deploy/.env.example — either the file moved or the "
            f"{PLACEHOLDER_MARKER!r} convention changed. Either way this guard is now measuring "
            "nothing, and would pass while every published secret sailed through"
        )
        accepted = [
            var for var, value in placeholders.items() if not is_published_placeholder(value)
        ]
        assert not accepted, (
            f"these values are printed in the repository and the guard accepts them: {accepted}"
        )

    def test_the_file_still_publishes_the_two_secrets_this_exists_for(self) -> None:
        """==The control for the sweep above.== Were those keys renamed, it would stay green while
        covering neither of the two secrets that made this a finding."""
        placeholders = published_placeholders()

        assert "AETHERCAL_APP_SECRET" in placeholders
        assert "AETHERCAL_BOOKING_SECRET" in placeholders


class TestTheGuard:
    def test_a_real_secret_is_accepted(self) -> None:
        """==The anti-vacuity half.== A guard that refused everything would pass every test above
        and stop every deployment on earth."""
        assert not is_published_placeholder(REAL_SECRET)
        assert (
            assert_not_published_placeholder(REAL_SECRET, env_var="AETHERCAL_APP_SECRET")
            == REAL_SECRET
        )

    def test_the_refusal_names_the_variable_and_says_why(self) -> None:
        with pytest.raises(PublishedPlaceholderError) as raised:
            assert_not_published_placeholder(
                "CHANGE_ME_LONG_RANDOM_SECRET", env_var="AETHERCAL_APP_SECRET"
            )

        assert "AETHERCAL_APP_SECRET" in str(raised.value)

    def test_the_marker_is_matched_anywhere_in_the_value(self) -> None:
        """The published URLs carry the marker INSIDE them
        (``postgresql://user:CHANGE_ME_APP_PASSWORD@host/db``), so an equality test against the
        placeholder string would miss every one of them."""
        assert is_published_placeholder("postgresql://u:CHANGE_ME_APP_PASSWORD@postgres:5432/db")

    def test_it_does_not_pretend_to_judge_strength(self) -> None:
        """==The boundary, pinned.== ``password`` is a terrible secret and this guard accepts it:
        the question is "did the repository publish this", not "is this any good".

        Written as a test because the next reader's instinct is to add an entropy check here — and
        that is how a guard starts rejecting real secrets, gets switched off, and takes the one real
        rule down with it.
        """
        assert not is_published_placeholder("password")
