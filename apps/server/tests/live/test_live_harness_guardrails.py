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


@pytest.fixture
def refund_settle_budget() -> tuple[int, float]:
    """Override the conftest budget: these controls must not spend ten real seconds waiting."""
    return (2, 0.0)


def _stripe_double(
    monkeypatch: pytest.MonkeyPatch, *, captured: int, refunds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A tiny Stripe stand-in for the cleanup path. Returns the live refund list, so a test can
    watch what the cleanup does to it — a POST appends, exactly as the real API would."""

    def _respond(payload: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, json=payload, request=httpx.Request("GET", "https://api.stripe.com/v1/x")
        )

    def _get(_self: httpx.Client, url: str, **_kwargs: Any) -> httpx.Response:
        if url.startswith("/payment_intents"):
            return _respond({"amount_received": captured, "latest_charge": "ch_DOUBLE"})
        if url.startswith("/refunds"):
            return _respond({"data": refunds})
        raise AssertionError(f"the cleanup asked for an unexpected URL: {url}")

    def _post(_self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        assert url == "/refunds", url
        # Whatever it issues arrives PENDING and never settles — the worst realistic case.
        issued = {
            "id": f"re_ISSUED_{len(refunds)}",
            "status": "pending",
            "amount": int(kwargs["data"]["amount"]),
        }
        refunds.append(issued)
        return _respond(issued)

    monkeypatch.setattr(httpx.Client, "get", _get)
    monkeypatch.setattr(httpx.Client, "post", _post)
    return refunds


def test_a_partial_refund_does_not_count_as_the_money_being_back(
    monkeypatch: pytest.MonkeyPatch, ensure_refunded: Callable[[str, str], str | None]
) -> None:
    """==The finding, reproduced.== 40 of 100 cents refunded is 60 cents still on somebody's card.

    The old cleanup returned success on *finding a refund*, without ever reading its amount — so a
    partial refund passed, the alarm never fired, and the money stayed put. That is the silent no-op
    in its purest form: the cleanup "worked" and did nothing.

    This also proves the top-up happens: the cleanup must ISSUE the missing 60 rather than merely
    complain about it.
    """
    refunds = _stripe_double(
        monkeypatch,
        captured=100,
        refunds=[{"id": "re_PARTIAL", "status": "succeeded", "amount": 40}],
    )

    problem = ensure_refunded("pi_PARTIAL", "refund:pi_PARTIAL")

    assert problem is not None, (
        "a 40-of-100 refund was accepted as complete. 60 cents are still held and nothing would "
        "have raised the alarm."
    )
    assert "60 of 100" in problem, problem
    assert [r["amount"] for r in refunds if r["id"].startswith("re_ISSUED")] == [60], (
        "the cleanup must issue the outstanding 60 cents, not merely report them"
    )


def test_a_pending_refund_does_not_count_as_the_money_being_back(
    monkeypatch: pytest.MonkeyPatch, ensure_refunded: Callable[[str, str], str | None]
) -> None:
    """==``pending`` is not terminal, and it is not the money being back.==

    A pending refund can still fail. Counting it as success is how a run reports green while the
    charge is untouched — and it is what the old cleanup did, explicitly, by treating ``pending``
    and ``succeeded`` as the same thing.

    Note that nothing new is issued here: the full amount is already in flight, so the cleanup waits
    for it and then reports that it never settled. Issuing a second refund on top would be the
    opposite mistake.
    """
    refunds = _stripe_double(
        monkeypatch,
        captured=100,
        refunds=[{"id": "re_PENDING", "status": "pending", "amount": 100}],
    )

    problem = ensure_refunded("pi_PENDING", "refund:pi_PENDING")

    assert problem is not None, "a pending refund was accepted as if the money had come back"
    assert "100 of 100" in problem, problem
    assert "in flight" in problem, problem
    assert not [r for r in refunds if r["id"].startswith("re_ISSUED")], (
        "the whole amount was already in flight; issuing another refund would double it"
    )


def test_a_fully_succeeded_refund_is_accepted(
    monkeypatch: pytest.MonkeyPatch, ensure_refunded: Callable[[str, str], str | None]
) -> None:
    """==The anti-vacuity half.== A cleanup that reported a problem every time would pass both
    controls above while making phase B impossible to complete.

    Two succeeded refunds summing to the capture also proves the tally ADDS them rather than
    stopping at the first one it sees.
    """
    _stripe_double(
        monkeypatch,
        captured=100,
        refunds=[
            {"id": "re_ONE", "status": "succeeded", "amount": 40},
            {"id": "re_TWO", "status": "succeeded", "amount": 60},
        ],
    )

    assert ensure_refunded("pi_WHOLE", "refund:pi_WHOLE") is None


def test_a_failed_refund_counts_as_nothing_and_is_retried(
    monkeypatch: pytest.MonkeyPatch, ensure_refunded: Callable[[str, str], str | None]
) -> None:
    """A ``failed`` refund is terminal and the money did NOT move, so it must count as zero.

    Counting it as either succeeded or in-flight would leave the charge untouched: as succeeded, the
    alarm never fires; as in-flight, the cleanup waits for something that will never arrive and
    never re-issues.
    """
    refunds = _stripe_double(
        monkeypatch,
        captured=100,
        refunds=[{"id": "re_FAILED", "status": "failed", "amount": 100}],
    )

    problem = ensure_refunded("pi_FAILED", "refund:pi_FAILED")

    assert problem is not None
    assert [r["amount"] for r in refunds if r["id"].startswith("re_ISSUED")] == [100], (
        "a failed refund moved no money, so the full amount must be issued again"
    )
