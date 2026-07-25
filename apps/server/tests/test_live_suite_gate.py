"""The gate on the live-provider suite: it must never dial out by accident, and never lie by skip.

``apps/server/tests/live/`` is the one place in this repository allowed through the repo-wide
network guard, because it exists to produce the evidence
``services/tenant_credentials.live_verifications()`` demands before a live payment credential may be
stored. An exception like that earns two obligations, and this file is both of them.

.. rubric:: ==1. It must not run when nobody asked it to==

The marker OPENS THE DOOR; it does not decide whether to walk through. Skipping is a fixture's job,
and ==that distinction was not academic here==: the control test originally took no fixture, so it
had nothing to skip on, and a plain offline ``pytest`` on a machine with no credentials **opened a
TLS connection to api.stripe.com**. It passed, which is the worst way for it to have gone wrong.
``test_network_guard`` already records the same lesson from the db suite — *"the marker is not a
guard"* — and this is that sentence collected for the second time.

.. rubric:: ==2. It must not report success for a run that exercised nothing==

``pytest -m live_provider`` with no key skips everything and exits 0. For the db suite that is a
green gate which caught no bugs; here it is worse, because ==somebody reads that green and writes it
into the register== the money guard consults. The root ``conftest.py`` turns it into a hard error,
and the subprocess test below is what proves the error really fires — in a real process, with the
variable really unset, checking the exit code CI would really read.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import aiosmtplib
import httplib2
import httpx
import pytest
from pytest_network_guard import RealNetworkForbiddenError, _forbidden

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIVE_TESTS = "apps/server/tests/live"
KEY_ENV = "AETHERCAL_LIVE_STRIPE_SECRET_KEY"


def _env_without_any_live_credential() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != KEY_ENV}


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=_env_without_any_live_credential(),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_a_plain_run_of_the_live_suite_reaches_no_provider() -> None:
    """==The regression, reproduced.== With no key, every live test SKIPS — none of them runs.

    A real process, the variable genuinely unset, and the outcome read from pytest's own summary.
    ==A PASS here would BE the bug==: the only way a test in that directory can pass without a
    credential is by having reached the real API and got an answer, which is exactly what happened
    before the control took the ``secret_key`` fixture whose value it does not use.

    It asserts on "did anything pass" rather than on a count, so a test added to that directory
    tomorrow is covered by this the day it is written.
    """
    result = _pytest(LIVE_TESTS, "-q")

    assert result.returncode == 0, result.stdout + result.stderr
    summary = result.stdout + result.stderr
    assert "passed" not in summary, (
        "a live-provider test PASSED with no credential configured. The only way that can happen "
        "is by reaching the real provider API — the network guard is lifted for that directory, "
        f"so a test with nothing to skip on dials straight out. Summary:\n{summary}"
    )
    assert "skipped" in summary, f"nothing was collected at all, so this proved nothing:\n{summary}"


def test_pytest_m_live_provider_without_a_credential_exits_non_zero() -> None:
    """==The other half: asking for the suite by name and being unable to run it is an ERROR.==

    Without this, the command that is supposed to GENERATE the evidence exits 0 having generated
    none — and the next step after a green run is somebody recording a verification in
    ``live_verifications()``. That is the single edit standing between this product and a real
    charge on a real card, so the report it rests on may not be a skip.
    """
    result = _pytest("-m", "live_provider", "--collect-only")

    assert result.returncode != 0, (
        "`pytest -m live_provider` exited 0 with no credential configured: every live test "
        "skipped, nothing was exercised, and the run would read as evidence that it had been."
    )
    assert KEY_ENV in (result.stdout + result.stderr)


@pytest.mark.live_provider
async def test_the_live_exception_keeps_the_doors_that_write_to_people_shut() -> None:
    """==The hole is narrow, and this is where "narrow" is checked rather than described.==

    A live-provider test may reach an HTTP API. It may NOT reach SMTP or the Google API: those write
    to a real person's inbox and to somebody's real calendar, and ==wanting to talk to Stripe is no
    reason to open them==. A charge can be refunded; an email cannot be unsent.

    Needs no credential and no network — it asserts what the guard did to this very test.
    """
    client = aiosmtplib.SMTP(hostname="smtp.example.com", port=587)
    with pytest.raises(RealNetworkForbiddenError):
        await client.connect()

    with pytest.raises(RealNetworkForbiddenError):
        httplib2.Http().request("https://www.googleapis.com/calendar/v3/users/me/calendarList")

    assert httpx.AsyncHTTPTransport.handle_async_request is not _forbidden, (
        "the HTTP door is shut for a live-provider test, so the verification harness cannot reach "
        "any provider and the money guard could never be discharged by evidence"
    )


def test_an_ordinary_test_has_every_door_shut_including_http() -> None:
    """==The mirror, and the anti-vacuity half.== The exception is keyed on the MARKER, and this
    test does not carry it — so the HTTP door is closed here even though it is open one test above.

    Without this, the assertion up there would be satisfied just as well by a guard that had simply
    stopped patching httpx for everybody.
    """
    assert httpx.AsyncHTTPTransport.handle_async_request is _forbidden
    assert httpx.HTTPTransport.handle_request is _forbidden
    assert aiosmtplib.SMTP.connect is _forbidden
    assert httplib2.Http.request is _forbidden
