"""==A value this repository publishes is not a secret.== The one place that answers, for everyone.

.. rubric:: The defect: the fix for a published key, wearing different clothes

The booking page let FastHTML mint its cookie-signing key into a ``.sesskey`` file; the file was
committed, and production signed cookies with a key anybody could read on GitHub. Making the key a
required environment variable closed that — and reopened it one step to the left.

``deploy/.env.example`` ships ``AETHERCAL_APP_SECRET=CHANGE_ME_LONG_RANDOM_SECRET``. The
quickstart's first instruction is ``cp .env.example .env``. Any non-blank string passed every check
there was. So an operator who never edited the placeholder got an instance running on a secret
==printed in a public repository== — and got it ==by omission==, which is the only way this failure
ever arrives. Nobody decides to publish their key. They just do not change the example.

``AETHERCAL_APP_SECRET`` is the worse half: the Fernet key is a pure function of it, so the
published placeholder yields a key anybody can derive from a clone and use to read every business's
Stripe and Mercado Pago credentials out of a database dump. The encryption's stated value — *"a
stolen dump is useless without the app secret"* — is void when the app secret is one we hand out.

.. rubric:: ==The rule is "this value is PUBLIC". It is not "this value is weak".==

No entropy score, no length floor, no dictionary. This module answers one narrow, factual question:
*is this the value the repository itself gives the world?* That is answerable. "Is this secret any
good?" is not — and a guard that guessed at strength would reject real generated secrets, be
switched off by the first operator it blocked, and take this rule down with it. ``password`` passes
here. That is correct: we did not publish it.

The marker is the repository's OWN convention (``CHANGE_ME``), read rather than re-stated, so the
answer comes from how the file already marks its placeholders instead of from a list of them that
would be stale the day a tenth is added. ``packages/aethercal-core/tests/test_published_secrets.py``
parses ``deploy/.env.example`` and requires every placeholder in it to be refused here — and fails
loudly if it parses none, rather than passing by measuring an empty set.

.. rubric:: What this deliberately does NOT guard

``AETHERCAL_PREVIOUS_APP_SECRET`` — the retiring secret during a key rotation. An instance that has
been running on the published placeholder must set it to *the placeholder* in order to rotate OFF
it, so guarding that field would refuse the one command that escapes this bug. A guard on the fire
exit is not security. See ``apps/server/tests/test_published_placeholder_boot.py``.

The other published placeholders (the database URLs, the SMTP and Google credentials, the superuser
password) are not guarded either, and the distinction is the one this whole batch is about:
==a placeholder there fails LOUDLY== — the connection is refused, the authentication fails, nothing
works and everybody knows within seconds. A placeholder in a SIGNING or ENCRYPTION key fails
**silently**: the instance runs perfectly and is simply not secret. This guard is for the values
whose only job is to be unguessable, because those are the ones whose failure says nothing at all.

.. rubric:: ==Why this lives in core, and the tension in that==

Two processes ask this question — the server (``AETHERCAL_APP_SECRET``) and the booking page
(``AETHERCAL_BOOKING_SECRET``) — and they must not answer it twice: two copies of a rule drift, and
neither goes red when they do. That is the lesson this batch is made of.

``aethercal.core`` is the only layer both may import (the layered contract puts ``server`` and
``booking`` as independent siblings above it, and both already depend on it — the booking page
transitively, via ``client`` → ``schemas`` → ``core``). It is also, honestly, a calendar domain
engine, and a deployment-configuration guard is not calendar domain. The placement is a judgement
call: this module is pure and zero-I/O, which is core's actual published contract, and the
alternatives — inventing a package for one function, or writing the rule twice — are worse on the
axis that matters here. If a shared configuration layer ever exists, this belongs there.

(The module is ``placeholders``, not ``secrets``: five modules in this codebase ``import secrets``
from the standard library. Absolute imports mean a sibling named ``secrets`` would not actually
shadow it — but a reader should not have to know that in order to be sure.)
"""

from __future__ import annotations

PLACEHOLDER_MARKER = "CHANGE_ME"
"""The repository's own mark for "you are supposed to replace this".

==Read as a convention, not re-listed as values.== Matching the marker rather than the exact strings
``deploy/.env.example`` currently ships is what makes the next placeholder covered on the day
somebody adds it, instead of on the day it reaches production.
"""


class PublishedPlaceholderError(ValueError):
    """A configured secret is one this repository publishes. ==Refuse to start.==

    A ``ValueError`` so pydantic surfaces it as a settings validation failure like any other, and so
    the booking page's plain ``from_env`` raises the same type — one exception for one question,
    across both processes.
    """


def is_published_placeholder(value: str) -> bool:
    """Is ``value`` one this repository hands out? ==The whole rule, in one predicate.=="""
    return PLACEHOLDER_MARKER in value


def assert_not_published_placeholder(value: str, *, env_var: str) -> str:
    """Return ``value``, or refuse it because this repository published it.

    Names the VARIABLE, never the value — not because this particular value is sensitive (it is the
    opposite: it is printed in a public file) but because this message gets copied by whoever adds
    the next secret, and that one will not be public. Echoing what you were handed is a habit, and a
    habit only has to be right once to be wrong.
    """
    if is_published_placeholder(value):
        raise PublishedPlaceholderError(
            f"{env_var} is still set to the placeholder that ships in deploy/.env.example.\n"
            "\n"
            "==That is not a weak secret. It is a PUBLISHED one== — the literal string is printed "
            "in a public repository, so anybody can reproduce whatever is derived from it. "
            "Refusing to start.\n"
            "\n"
            "Generate a real one and put it in your .env:\n"
            "\n"
            '    python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
            "\n"
            "Each secret in that file needs its OWN value. They are separate on purpose, so that "
            "learning one does not hand over the rest."
        )
    return value


__all__ = [
    "PLACEHOLDER_MARKER",
    "PublishedPlaceholderError",
    "assert_not_published_placeholder",
    "is_published_placeholder",
]
