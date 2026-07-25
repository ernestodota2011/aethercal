"""Shared machinery for the live-provider harness. ==The barriers live here, not in each test.==

Everything in ``tests/live/`` talks to a real provider with a real credential, and one of its phases
moves **real money** (a $1 charge, refunded). The controls that make that acceptable are fixtures
rather than conventions, because a convention is something each new test has to remember and a
fixture is something it cannot get around.

.. rubric:: ==The hard cap is the ABSENCE OF A KNOB, not an assertion somebody must write==

:func:`open_one_dollar_session` takes no ``amount_cents``. A test cannot create a session for any
other figure, because there is nothing to pass — the same shape as ``resolve_money_credential``
having no ``instance_default``. A units bug (``100`` meant as cents, read as dollars) is what turns
a $1 verification into a **$100** one, and it is exactly the kind of mistake that arrives by
inattention: nobody decides to charge a hundred dollars, they mistype a constant. So the constant is
asserted against its literal before every creation, and the amount is not a parameter of the seam at
all.

.. rubric:: ==Refund is unplugged by default, and re-plugged only where it is the thing under test==

``StripeGateway.refund`` is the one call that moves money, so :func:`_refund_unplugged` (autouse)
makes it raise for every test in this directory. Phase B — the test whose *purpose* is to exercise
it — asks for :func:`refund_reconnected`, which restores it for that test alone and hands it back
unplugged at teardown.

.. rubric:: ==Cleanup never masks the failure it was cleaning up after==

:func:`ensure_refunded` and :func:`expire_session` are written to be called from a ``finally`` and
==never raise==. They report a problem as a string, and the caller decides: if the body already
failed, the problem is SHOUTED and the original exception travels intact; if the body passed, the
cleanup problem becomes the failure. Money stuck behind a green test is the worst outcome this
harness can produce, and a cleanup error that swallows a real one is the second worst.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Coroutine, Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.stripe import StripeGateway
from aethercal.server.services.payments import CheckoutSession

SECRET_KEY_ENV = "AETHERCAL_LIVE_STRIPE_SECRET_KEY"
STRIPE_API_BASE = "https://api.stripe.com/v1"
DASHBOARD_PAYMENT_URL = "https://dashboard.stripe.com/payments"
HTTP_TIMEOUT = httpx.Timeout(30.0)

ONE_DOLLAR_CENTS = 100
"""==The hard cap.== The only amount this harness may ever create, in the currency's minor unit."""

CURRENCY = "usd"
"""Fixed alongside the cap: 100 minor units is $1.00 in USD and something else elsewhere."""

RETURN_URL = "https://example.com/aethercal-live-verification"
"""Where the guest would be sent back to. Stripe requires a well-formed URL and never fetches it."""

REFUND_SUCCEEDED = "succeeded"
"""==The ONLY status that means the money is back.== Terminal, and the money has actually moved."""

REFUND_DEAD_ENDS = frozenset({"failed", "canceled"})
"""Terminal and the money did NOT move — so these count as zero, never as progress.

Everything that is neither this nor :data:`REFUND_SUCCEEDED` (``pending``, ``requires_action``) is
**in flight**: real money that has not come back yet. Counting it as done is the exact bug this
distinction exists to prevent."""

_REAL_REFUND = StripeGateway.refund
"""Captured at import, before the autouse fixture below can unplug it."""

OpenSession = Callable[..., Coroutine[Any, Any, CheckoutSession]]


def _assert_the_hard_cap() -> None:
    """==Run before anything is created.== The constant is checked against its literal.

    Belt to :func:`open_one_dollar_session`'s braces: the seam removes the *choice* of an amount,
    and this catches the other way the number could go wrong — somebody editing the constant.
    """
    assert ONE_DOLLAR_CENTS == 100, (
        f"the live harness's hard cap has been changed to {ONE_DOLLAR_CENTS}. This figure is "
        "charged to a real card on a real account. It is one dollar, in cents, and a change here "
        "is a decision about somebody's money — not a test parameter."
    )
    assert CURRENCY == "usd", (
        f"the hard cap is {ONE_DOLLAR_CENTS} MINOR UNITS, which is $1.00 in USD and a different "
        f"sum in {CURRENCY!r}. Changing the currency without re-deriving the cap is the units bug "
        "this assertion exists to catch."
    )


@pytest.fixture
def secret_key() -> str:
    """The provider key, from the ENVIRONMENT. ==Never from ``argv``, never from this repo.==

    Skipping without it keeps the contract the ``db`` fixtures keep: an ordinary run on an ordinary
    machine stays quiet and offline. Asking for this suite BY NAME without a key is the root
    ``conftest.py``'s business, and it is an error there rather than a skip.

    ==The skip rides on this fixture, and that is load-bearing.== The marker only opens the network
    door; it skips nothing. A test in this directory that does not depend on this fixture has
    nothing to stop it and will dial out on a machine with no credentials at all — which is exactly
    what happened once, and what ``test_live_suite_gate`` now watches for.
    """
    key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if not key:
        pytest.skip(
            f"{SECRET_KEY_ENV} is not set, so there is no real API to exercise. Export the key to "
            "run the verification harness."
        )
    return key


@pytest.fixture(autouse=True)
def _refund_unplugged(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The call that moves money raises, everywhere in this directory, unless asked for.==

    "This harness only makes zero-cost calls" is otherwise a claim about what somebody remembered to
    write. This makes it a claim about what the process CAN do: a copy-pasted line, a helpful future
    addition, or a fixture that reaches for the gateway cannot issue a refund against a real
    account.

    Phase B overrides it deliberately, for itself only, via :func:`refund_reconnected`.
    """

    async def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "this test may not issue a refund. `StripeGateway.refund` moves real money and is "
            "unplugged for the whole live harness; the ONE test whose purpose is to exercise it "
            "asks for the `refund_reconnected` fixture, which says so in its own signature."
        )

    monkeypatch.setattr(StripeGateway, "refund", _refuse, raising=True)


@pytest.fixture
def refund_reconnected(_refund_unplugged: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the REAL ``StripeGateway.refund`` for one test. ==Phase B, and nothing else.==

    It depends on :func:`_refund_unplugged` by name so the ordering is stated rather than assumed:
    the unplug happens first, this undoes it, and ``monkeypatch`` unwinds both in reverse at
    teardown — so the refund is unplugged again the moment this test ends.
    """
    monkeypatch.setattr(StripeGateway, "refund", _REAL_REFUND, raising=True)


@pytest.fixture
def gateway() -> StripeGateway:
    """The real production object: no transport, exactly as ``integrations.money`` builds it."""
    return StripeGateway()


@pytest.fixture
def stripe_api(secret_key: str) -> Iterator[httpx.Client]:
    """A raw client for CONFIRMING what the gateway did, independently of the gateway.

    ==Deliberately not the gateway's own client.== Asking the code under test whether the code under
    test worked is the shape of a probe that agrees with itself. Sessions and refunds are made
    through ``StripeGateway``; every confirmation is a separate, hand-built request, so the answer
    comes from Stripe rather than from a return value.

    It is also the CLEANUP path, and that matters more than the symmetry: when the gateway is the
    thing that turns out to be broken, the money still has to come back.
    """
    with httpx.Client(
        base_url=STRIPE_API_BASE,
        headers={"Authorization": f"Bearer {secret_key}"},
        timeout=HTTP_TIMEOUT,
    ) as client:
        yield client


@pytest.fixture
def one_dollar_cents() -> int:
    """The hard cap, handed to tests as a fixture. ==Nothing in this directory re-types ``100``.==

    A test that wrote the literal itself would be a second place the figure lives, and the two would
    drift the day somebody changed one. This is the only amount any assertion here compares against.
    """
    _assert_the_hard_cap()
    return ONE_DOLLAR_CENTS


@pytest.fixture
def unauthenticated_stripe_api() -> Iterator[httpx.Client]:
    """A client carrying a key Stripe never issued. ==For the control, and it creates nothing.==

    The bearer below is a literal, not a redaction: there is no real credential in this repository.
    Used only to demand a ``401`` from the real API, which is the one answer no stub, proxy or
    offline cache can fabricate.
    """
    with httpx.Client(
        base_url=STRIPE_API_BASE,
        headers={"Authorization": "Bearer sk_test_THIS_KEY_WAS_NEVER_ISSUED_BY_STRIPE"},
        timeout=HTTP_TIMEOUT,
    ) as client:
        yield client


@pytest.fixture
def open_one_dollar_session(secret_key: str, gateway: StripeGateway) -> OpenSession:
    """Open a Checkout Session through the real gateway. ==There is no ``amount_cents`` to pass.==

    The hard cap is enforced by the SHAPE of this seam and not by a rule a test has to follow: a
    test that wanted to create a $100 session would have to stop using this fixture, which is a
    visible edit rather than a mistyped literal.
    """

    async def _open(*, idempotency_key: str, expires_at: datetime) -> CheckoutSession:
        _assert_the_hard_cap()
        return await gateway.create_checkout_session(
            idempotency_key=idempotency_key,
            amount_cents=ONE_DOLLAR_CENTS,
            currency=CURRENCY,
            expires_at=expires_at,
            return_url=RETURN_URL,
            secrets={"secret_key": secret_key},
        )

    return _open


def _problem(action: str, exc: Exception) -> str:
    return f"{action} failed: {type(exc).__name__}: {exc}"


@pytest.fixture
def expire_session(stripe_api: httpx.Client) -> Callable[[str], str | None]:
    """Expire a Checkout Session. ==Written for a ``finally``: it reports, it never raises.==

    An open LIVE session is a payable invitation left standing in a real account, so it is cleaned
    up even when the test that created it has already failed — and cleaning up must not replace the
    failure that is already on its way out.
    """

    def _expire(session_id: str) -> str | None:
        try:
            response = stripe_api.post(f"/checkout/sessions/{session_id}/expire")
            response.raise_for_status()
            status = response.json().get("status")
        except Exception as exc:
            return _problem(f"expiring checkout session {session_id}", exc)
        if status != "expired":
            return f"checkout session {session_id} is {status!r} after being expired, not 'expired'"
        return None

    return _expire


@pytest.fixture
def refund_settle_budget() -> tuple[int, float]:
    """How long :func:`ensure_refunded` waits for a refund to reach a TERMINAL state.

    ``(attempts, seconds between them)``. Card refunds are usually ``succeeded`` on the first read;
    the budget exists for the ones that are not, because ==``pending`` is not "the money is back"==
    and treating it as such is precisely the silent no-op this fixture exists to prevent.

    A test overrides this by declaring a fixture of the same name in its own module.
    """
    return (10, 1.0)


@pytest.fixture
def ensure_refunded(
    stripe_api: httpx.Client, refund_settle_budget: tuple[int, float]
) -> Callable[[str, str], str | None]:
    """Guarantee the money went back — ==all of it, and definitively.== Never raises.

    .. rubric:: ==The bug this replaces: "a refund exists" is not "the money is back"==

    The first cut returned success on finding ANY refund whose status was ``succeeded`` **or
    ``pending``**, without ever looking at the amount. Both halves of that are wrong, and each one
    breaks the only promise this directory makes:

    * a **partial** refund satisfied it. Stripe will happily refund 40 of 100 cents; the old check
      saw one refund, called it done, and 60 cents stayed on the card with no alarm;
    * a **pending** refund satisfied it. Pending is not terminal — it can still fail — so the
      cleanup declared victory over money that had not moved.

    ==The cleanup "worked" and did nothing==, which is the shape this codebase keeps finding
    (``feedback_no_op_fail_closed``). Worse than a loud failure, because the alarm below never fires
    and nobody goes looking.

    .. rubric:: What it does instead

    1. reads what was actually **captured** (``amount_received`` on the PaymentIntent) — the figure
       that has to come back, taken from Stripe rather than assumed to be the hard cap;
    2. sums only refunds that are ``succeeded``. ``pending`` is counted separately, as *in flight*;
       ``failed``/``canceled`` are counted as nothing at all;
    3. issues a refund for whatever is neither refunded nor in flight, ==on its own idempotency
       key== (derived from the caller's, plus the amount) — a different amount cannot reuse the key
       that was minted for the full refund, and deriving it keeps a retry of THIS top-up idempotent;
    4. polls until the succeeded total covers the capture, or the budget runs out;
    5. and if anything is still outstanding or merely pending, ==returns a problem== so the caller
       shouts the charge id and fails.

    ==It deliberately does NOT go through ``StripeGateway``.== Phase B exists precisely because that
    adapter has never been run for real; if it is broken, this is what still gets the dollar back. A
    cleanup path that shares the fault of the thing it is cleaning up after is not a cleanup path.
    """
    attempts, delay = refund_settle_budget

    def _captured_cents(payment_intent_id: str) -> tuple[int | None, str | None]:
        try:
            response = stripe_api.get(f"/payment_intents/{payment_intent_id}")
            response.raise_for_status()
            received = response.json().get("amount_received")
        except Exception as exc:
            return None, _problem(f"reading payment intent {payment_intent_id}", exc)
        if not isinstance(received, int):
            return None, (
                f"payment intent {payment_intent_id} reports amount_received={received!r}, so "
                "there is no way to tell how much has to come back"
            )
        return received, None

    def _tally(payment_intent_id: str) -> tuple[int, int, str | None]:
        """``(succeeded cents, in-flight cents, problem)`` — terminal success counted separately."""
        try:
            response = stripe_api.get(
                "/refunds", params={"payment_intent": payment_intent_id, "limit": 100}
            )
            response.raise_for_status()
            refunds = response.json().get("data", [])
        except Exception as exc:
            return 0, 0, _problem(f"listing refunds for {payment_intent_id}", exc)

        succeeded = in_flight = 0
        for refund in refunds:
            amount = refund.get("amount")
            if not isinstance(amount, int):
                continue
            status = refund.get("status")
            if status == REFUND_SUCCEEDED:
                succeeded += amount
            elif status not in REFUND_DEAD_ENDS:
                in_flight += amount  # pending / requires_action: real money, not yet back
        return succeeded, in_flight, None

    def _ensure(payment_intent_id: str, idempotency_key: str) -> str | None:  # noqa: PLR0911
        # Seven exits, and each one answers a different question about somebody's money: could not
        # read it, nothing was taken, could not list it, could not re-issue it, still not settled,
        # settled in full. Collapsing them would make the alarm's message vaguer, and the message is
        # what an operator acts on at the moment a dollar is stuck.
        captured, problem = _captured_cents(payment_intent_id)
        if problem is not None:
            return problem
        if not captured:
            return None  # nothing was ever taken, so nothing is being held

        succeeded, in_flight, problem = _tally(payment_intent_id)
        if problem is not None:
            return problem

        uncovered = captured - succeeded - in_flight
        if uncovered > 0:
            # ==Its own key.== The caller's was minted for the FULL refund; reusing it for a
            # different amount is an idempotency conflict, and a fresh random one would let a retry
            # of this top-up refund twice. Derived = stable across retries, distinct per amount.
            try:
                made = stripe_api.post(
                    "/refunds",
                    data={"payment_intent": payment_intent_id, "amount": uncovered},
                    headers={"Idempotency-Key": f"{idempotency_key}:outstanding:{uncovered}"},
                )
                made.raise_for_status()
            except Exception as exc:
                return _problem(
                    f"refunding the outstanding {uncovered} cents of {payment_intent_id}", exc
                )

        for attempt in range(attempts):
            succeeded, in_flight, problem = _tally(payment_intent_id)
            if problem is not None:
                return problem
            if succeeded >= captured:
                return None  # ==terminal, and it covers the whole capture==
            if attempt < attempts - 1:
                time.sleep(delay)

        outstanding = captured - succeeded
        return (
            f"{outstanding} of {captured} cents are STILL NOT refunded for {payment_intent_id} "
            f"({succeeded} succeeded, {in_flight} in flight and not yet terminal). The money has "
            "NOT come back."
        )

    return _ensure


@pytest.fixture
def shout_that_money_is_held(stripe_api: httpx.Client) -> Callable[[str, str], None]:
    """==If the money could not be returned, it is never allowed to be quiet about it.==

    A failing harness that leaves $1 sitting on somebody's card without saying so is the worst
    result this directory can produce — worse than a red test, because nobody goes looking. This
    prints the charge id and the one-click place to fix it by hand.

    It resolves the charge defensively: if even that lookup fails it shouts with the PaymentIntent,
    which is enough to find the payment in the dashboard.
    """

    def _shout(payment_intent_id: str, problem: str) -> None:
        charge_id = payment_intent_id
        try:
            intent = stripe_api.get(f"/payment_intents/{payment_intent_id}")
            intent.raise_for_status()
            charge_id = str(intent.json().get("latest_charge") or payment_intent_id)
        except Exception:
            pass
        print(
            "\n"
            "################################################################\n"
            "###  MONEY IS STILL HELD. REFUND IT BY HAND, NOW.            ###\n"
            "################################################################\n"
            f"  charge         : {charge_id}\n"
            f"  payment_intent : {payment_intent_id}\n"
            f"  dashboard      : {DASHBOARD_PAYMENT_URL}/{payment_intent_id}\n"
            f"  why            : {problem}\n"
            "  The automatic refund did not complete. This is real money on a real\n"
            "  card, and nothing else in this harness will retry it.\n"
            "################################################################\n"
        )

    return _shout
