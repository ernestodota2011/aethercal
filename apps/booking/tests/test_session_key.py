"""The session cookie signing key comes from the ENVIRONMENT, and can never come from a file.

.. rubric:: The finding: a signing key that published itself

FastHTML's ``FastHTML(...)`` takes ``secret_key=`` and, when it is not given one, calls
``get_key(key=None, fname='.sesskey')`` — *"Get session key from `key` param or read/create from
file"*. It then installs ``SessionMiddleware`` with whatever that returned. So a caller who simply
does not pass the parameter gets a key **minted into a file in the working directory**, and
``create_app`` did not pass it.

That file was committed. ``.sesskey`` reached the public repository (it rode in on an unrelated
commit), which means ``book.aetherlogik.com`` signed its session cookies with a key anybody could
read on GitHub.

.. rubric:: ==Nobody decided to publish a key. Nobody passed a parameter.==

That is the whole lesson, and it is the same one as ``LEND_OPERATOR_PHONE_IDENTITY``: ==the
dangerous thing must not be what happens when you do nothing==. Reading a key off disk is a
reasonable default for a notebook demo and a hole in a deployed product, and the distance between
the two was one keyword argument nobody had a reason to think about.

Today no route reads the session, so nothing is exploitable — and that is not a defence. The key is
compromised, the middleware signs with it regardless, and the first ``sess['...']`` anybody writes
(the natural thing to do in FastHTML) inherits a hole nobody will remember is there. ==A secret does
not stop being public because it is not being used yet.==

.. rubric:: What these tests pin, and why in this shape

The behavioural tests assert the EFFECT rather than the configuration: they check that no
``.sesskey`` is ever created, which is the observable of ``get_key`` never reaching the disk. A test
that grepped the source for ``secret_key=`` would pass against a second call site that forgot it, so
the AST guard covers that separately — deriving the rule from the code (walk every ``FastHTML``
call) rather than from a list of the call sites we happen to know about today.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

import aethercal.booking.app as booking_app
from aethercal.booking.app import create_app
from aethercal.booking.settings import BOOKING_SECRET_ENV, BookingSettings
from aethercal.client import AetherCalClient
from aethercal.core.placeholders import PublishedPlaceholderError

BOOKING_SRC = Path(str(booking_app.__file__)).parent

# Synthetic. Not a redaction of anything: there is no real key in this repository.
SECRET = "not-a-real-booking-secret-for-tests"

#: The literal `deploy/.env.example` hands every operator, and which `cp .env.example .env` puts
#: straight into production unless somebody remembers to edit it. Hard-coded HERE (rather than
#: parsed) on purpose: this test is about the booking page's REFUSAL. The sweep that proves the file
#: and the rule cannot drift apart is core's `test_published_secrets.py`, which reads the file.
PUBLISHED_PLACEHOLDER = "CHANGE_ME_LONG_RANDOM_BOOKING_SECRET"


def _settings(**overrides: object) -> BookingSettings:
    base: dict[str, object] = {
        "api_url": "http://api.test",
        "tenant_slug": "acme",
        "turnstile_site_key": None,
        "default_locale": "es",
        "app_secret": SECRET,
    }
    base.update(overrides)
    return BookingSettings(**base)  # type: ignore[arg-type]


def _build(settings: BookingSettings) -> Any:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    return create_app(
        settings=settings,
        client_factory=lambda: AetherCalClient(settings.api_url, transport=transport),
    )


class TestTheKeyComesFromTheEnvironment:
    def test_the_app_signs_with_the_secret_it_was_given(self) -> None:
        """==The effect, not the config.== FastHTML stores whatever ``get_key`` returned; our secret
        coming back out is the proof that the ``key`` param won and the file branch was never
        reached."""
        app = _build(_settings())

        assert app.secret_key == SECRET

    def test_building_the_app_never_creates_a_sesskey_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==The one that would have caught this.== ``get_key`` writes ``.sesskey`` into the CWD
        when it is given no key, so the file's absence proves it was never consulted.

        Asserting on the FILE rather than on the call is deliberate: it stays true however FastHTML
        spells its parameter, and it goes red for any future call site that forgets it.
        """
        monkeypatch.chdir(tmp_path)

        _build(_settings())

        assert not (tmp_path / ".sesskey").exists(), (
            "the booking app minted a session key onto disk — the file that ended up committed to "
            "a public repository, signing production's cookies"
        )
        assert list(tmp_path.iterdir()) == [], "nothing at all should have been written"

    def test_two_apps_built_from_the_same_secret_share_it(self) -> None:
        """==A cookie must survive a restart and a second replica.== The file-backed default gave
        each process a ``uuid4()`` of its own unless they shared a volume — so this also pins why
        the key is CONFIGURATION and not something generated at boot.
        """
        assert _build(_settings()).secret_key == _build(_settings()).secret_key


class TestTheSecretIsRequired:
    def test_from_env_without_the_secret_refuses_to_build(self) -> None:
        """==Fail the boot, exactly as the server does for ``app_secret``.== A default here — any
        default — is how the file-backed key happened: the unsafe thing arrived by omission."""
        with pytest.raises(ValueError, match=BOOKING_SECRET_ENV):
            BookingSettings.from_env({})

    def test_from_env_reads_the_secret(self) -> None:
        settings = BookingSettings.from_env({BOOKING_SECRET_ENV: SECRET})

        assert settings.app_secret == SECRET

    def test_a_blank_secret_is_not_a_secret(self) -> None:
        """Whitespace is the shape a half-filled ``.env`` takes; it must not read as "set".

        (There is deliberately no "the refusal does not echo the value" test here, unlike on the
        credential path. This refusal fires ONLY when the value is empty or blank, so there is
        never a secret in scope to leak — and a test asserting that whitespace was not echoed is
        one that cannot meaningfully fail. A guard for a risk that does not exist is noise, and
        noise is what gets deleted the day it goes red for an unrelated reason.)
        """
        with pytest.raises(ValueError, match=BOOKING_SECRET_ENV):
            BookingSettings.from_env({BOOKING_SECRET_ENV: "   "})


class TestThePublishedPlaceholderIsNotASecret:
    """==The same bug, one step to the left.== A key nobody can read, replaced by a key everybody
    can read.

    Making the secret required closed "FastHTML mints it onto disk". It did not close
    ``deploy/.env.example`` shipping ``AETHERCAL_BOOKING_SECRET=CHANGE_ME_...``, the quickstart
    saying ``cp .env.example .env``, and any non-blank string passing every check there was. The
    outcome is identical — cookies signed with a publicly known key — and it arrives the same way:
    ==by omission==. Nobody publishes their key on purpose; they just do not edit the placeholder.

    The rule lives in ``aethercal.core.placeholders`` because ``AETHERCAL_APP_SECRET`` had it too,
    and one question must not have two answers. The sweep over every published placeholder is
    ``packages/aethercal-core/tests/test_published_secrets.py``.
    """

    def test_the_published_placeholder_is_refused(self) -> None:
        with pytest.raises(PublishedPlaceholderError):
            BookingSettings.from_env({BOOKING_SECRET_ENV: PUBLISHED_PLACEHOLDER})

    def test_the_refusal_names_the_variable(self) -> None:
        with pytest.raises(PublishedPlaceholderError) as raised:
            BookingSettings.from_env({BOOKING_SECRET_ENV: PUBLISHED_PLACEHOLDER})

        assert BOOKING_SECRET_ENV in str(raised.value)

    def test_a_secret_the_operator_actually_generated_is_accepted(self) -> None:
        """==The anti-vacuity half==, and the reason this is not an entropy check: the guard must
        pass anything the repository did not publish."""
        settings = BookingSettings.from_env({BOOKING_SECRET_ENV: SECRET})

        assert settings.app_secret == SECRET

    def test_the_page_still_builds_from_a_real_secret(self) -> None:
        """End to end: a generated secret produces an app that signs with it and writes no file."""
        assert _build(_settings()).secret_key == SECRET


class TestNoCallSiteCanForgetIt:
    def test_every_fasthtml_construction_passes_secret_key(self) -> None:
        """==Derived from the code, not from a list.== Walk every ``FastHTML(...)`` call in the
        booking package and require ``secret_key=`` on each.

        The behavioural tests above cover ``create_app``. This one covers the call site that does
        not exist yet — because the defect was never that ``create_app`` was written wrong. It was
        that FastHTML's default is to read a key off disk, and nothing said so at the call site.
        """
        found = 0
        for source in BOOKING_SRC.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "FastHTML"):
                    continue
                found += 1
                keywords = {keyword.arg for keyword in node.keywords}
                assert "secret_key" in keywords, (
                    f"{source.name}:{node.lineno} builds FastHTML without secret_key=, so "
                    "fasthtml's get_key() mints one into a .sesskey file on disk — the exact "
                    "defect that published production's cookie-signing key"
                )
        assert found, (
            "no FastHTML construction found — this guard would pass by measuring nothing, which is "
            "the failure mode it exists to prevent"
        )
