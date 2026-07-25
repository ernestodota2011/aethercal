"""The guardrails on the money harness, checked. ==Offline, unmarked, and run on every commit.==

The other modules in this directory need a real credential and skip without one, which means their
safety properties would otherwise be verified only on the rare day somebody runs them by hand — and
those properties are the reason the harness is allowed to exist at all.

So the guardrails are tested HERE, and deliberately:

* **without** the ``live_provider`` marker, so they run in the ordinary suite and in CI. The
  ``conftest`` fixtures they exercise apply to this whole directory regardless of the marker;
* **without** any network call, so they need no key and reach nothing. The full network guard is
  armed for these tests, exactly as for any other ordinary test.

==A barrier nobody tests is a barrier nobody has.== Each of these can fail: remove the guardrail it
describes and the test goes red.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.stripe import StripeGateway

SECRET_KEY_ENV = "AETHERCAL_LIVE_STRIPE_SECRET_KEY"


@pytest.fixture(autouse=True)
def _a_dummy_key_so_the_client_fixtures_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``stripe_api`` construct without a real credential. ==Nothing here reaches the network.==

    Building an :class:`httpx.Client` opens no connection, and the tests below replace its request
    methods before any call. The value is a literal, not a redaction: there is no real credential in
    this repository.

    ==This does NOT weaken the skip that protects the live tests==: those live in other modules, and
    this fixture is scoped to this file. The network guard is fully armed for everything here.
    """
    monkeypatch.setenv(SECRET_KEY_ENV, "sk_test_NOT_A_REAL_KEY_guardrails")


async def test_the_refund_is_unplugged_for_this_whole_directory(gateway: StripeGateway) -> None:
    """==The call that moves money raises, and this proves the autouse fixture really bites.==

    Phase B re-plugs it deliberately, for itself alone. Everywhere else in this directory —
    including any test somebody adds tomorrow without reading the conftest — ``refund`` is a
    landmine that goes off harmlessly instead of a live call against a real account.
    """
    with pytest.raises(AssertionError, match="may not issue a refund"):
        await gateway.refund(
            provider_ref="pi_NOT_A_REAL_INTENT",
            idempotency_key="refund:pi_NOT_A_REAL_INTENT",
            secrets={"secret_key": "sk_test_NOT_A_REAL_KEY"},
        )


def test_the_hard_cap_cannot_be_passed_a_different_amount(
    open_one_dollar_session: Callable[..., Any],
) -> None:
    """==The cap is the ABSENCE OF A KNOB, and here is the absence.==

    An assertion inside a helper can be sidestepped by a caller that stops using the helper. A
    missing parameter cannot be passed by a caller at all — so the check is on the SIGNATURE, which
    is the thing that actually constrains what a test can do.

    A units bug is what turns $1 into $100, and it arrives by inattention: somebody types a number.
    There is no number to type.
    """
    parameters = inspect.signature(open_one_dollar_session).parameters

    assert "amount_cents" not in parameters, (
        "the live harness's session opener accepts an amount again. That is the hard cap gone: a "
        "test can now name a figure, and the figure is charged to a real card."
    )
    assert "currency" not in parameters, (
        "100 minor units is $1.00 in USD and a different sum elsewhere; the currency is fixed "
        "alongside the cap for that reason"
    )
    assert set(parameters) == {"idempotency_key", "expires_at"}, (
        f"the session opener takes {sorted(parameters)}. Anything beyond the idempotency key and "
        "the expiry is a lever over real money that a test should not have."
    )


def test_the_hard_cap_is_one_dollar(one_dollar_cents: int) -> None:
    """The figure itself, read from the one place the whole directory takes it from."""
    assert one_dollar_cents == 100


def test_expiring_a_session_reports_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, expire_session: Callable[[str], str | None]
) -> None:
    """==The ``finally`` contract: cleanup reports, it never raises.==

    This is what stops a cleanup failure from replacing the exception that explains it. If
    ``expire_session`` raised, a test that failed on a real assertion would surface as a confusing
    error from the cleanup path, and the actual defect would be gone.
    """

    def _explode(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("the network is down")

    monkeypatch.setattr(httpx.Client, "post", _explode)

    problem = expire_session("cs_NOT_A_REAL_SESSION")

    assert problem is not None, "a failed expiry must be REPORTED, not swallowed"
    assert "cs_NOT_A_REAL_SESSION" in problem, "the report must name what is still open"


def test_ensuring_a_refund_reports_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, ensure_refunded: Callable[[str, str], str | None]
) -> None:
    """The same contract, on the path where it matters most.

    ==If this raised, the shout would never happen.== The caller's ``finally`` calls it, reads the
    problem, and only then prints the charge id and the manual-refund instructions. An exception
    here would skip straight past the alarm and leave the money held in silence.
    """

    def _explode(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("the network is down")

    monkeypatch.setattr(httpx.Client, "get", _explode)

    problem = ensure_refunded("pi_NOT_A_REAL_INTENT", "refund:pi_NOT_A_REAL_INTENT")

    assert problem is not None, "a failed refund must be REPORTED so the caller can shout"
    assert "pi_NOT_A_REAL_INTENT" in problem, "the report must name the payment that is still held"
