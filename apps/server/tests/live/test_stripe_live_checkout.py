"""Exercise the REAL ``StripeGateway`` against the REAL Stripe API. ==Zero money moves.==

.. rubric:: What this is for

``services/tenant_credentials.live_verifications()`` refuses a live payment credential until
somebody has actually run the gateway against the provider. ==This is the run.== It is the only
thing in the repository that may put ``GatewayOperation.CHECKOUT`` into that register for Stripe,
and what it prints is the evidence that goes with it.

.. rubric:: ==Zero cost, and that is a property of the CALLS, not of an intention==

Creating a Checkout Session moves no money: a session is an *invitation* to pay, and nobody pays
this one. It is created, read back, and expired — the last so no live invitation is left standing in
the account. ==No card, no charge, no refund, no payout.==

``refund`` is deliberately NOT exercised, and cannot be from here: proving it needs a real charge to
refund, which is money leaving a real card, and that is a decision with a price attached rather than
a test. It stays unverified in the register, which is exactly what the per-operation granularity is
for — see ``docs/byok-credentials.md``. The autouse fixture below makes that structural rather than
merely intended.

.. rubric:: How to run it

.. code-block:: bash

   # The ENVIRONMENT, never a flag: an argument lands in the process table (`ps`), the shell's
   # history file and the terminal scrollback. `read -s` keeps it out of the history too.
   read -rs AETHERCAL_LIVE_STRIPE_SECRET_KEY && export AETHERCAL_LIVE_STRIPE_SECRET_KEY
   uv run pytest apps/server/tests/live -m live_provider -s

``-s`` because the evidence is printed and pytest swallows the output of passing tests. Without the
key these tests SKIP on an ordinary run; ``-m live_provider`` without it is a hard error (the root
``conftest.py``) rather than a green run that exercised nothing.

Both ``sk_test_`` and ``sk_live_`` keys work here, and the run reports which one it used. A
test-mode run proves the code path end to end but says nothing about live mode, so ``livemode`` is
REPORTED rather than assumed — it belongs in the evidence, and whoever writes the register decides
what that evidence supports.

.. rubric:: ==Every probe carries a control==

A "live" test that quietly reached a stub, a proxy, or nothing at all would produce evidence for
code that never ran — the worst possible failure here, because the evidence is the entire point. So
:func:`test_the_control_proves_this_process_really_reaches_stripe` runs first with a deliberately
invalid key and requires Stripe's own ``401``. Nothing local produces that.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.stripe import StripeGateway

pytestmark = pytest.mark.live_provider

SECRET_KEY_ENV = "AETHERCAL_LIVE_STRIPE_SECRET_KEY"
STRIPE_API_BASE = "https://api.stripe.com/v1"
HTTP_TIMEOUT = httpx.Timeout(30.0)

RETURN_URL = "https://example.com/aethercal-live-verification"
"""Where the (never-arriving) guest would be sent back to. Stripe requires a well-formed URL; it
never fetches it, and nobody ever lands on it because nobody pays this session."""

AMOUNT_CENTS = 100
"""A legal amount for the currency, and nothing more. ==Nobody pays this session==, so the figure
never becomes a charge; it exists because a Checkout Session must carry a line item."""


@pytest.fixture
def secret_key() -> str:
    """The provider key, from the ENVIRONMENT. ==Never from ``argv``, never from this repo.==

    Skipping without it keeps the same contract the ``db`` fixtures keep: an ordinary run on an
    ordinary machine stays quiet and offline. Asking for this suite BY NAME without a key is the
    root ``conftest.py``'s business, and it is an error there rather than a skip.
    """
    key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if not key:
        pytest.skip(
            f"{SECRET_KEY_ENV} is not set, so there is no real API to exercise. Export the key to "
            "run the verification harness (zero-cost calls only)."
        )
    return key


@pytest.fixture(autouse=True)
def _refund_cannot_be_reached_from_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The one operation that moves money is unplugged for the whole of this module.==

    "This harness only makes zero-cost calls" is otherwise a claim about what somebody remembered to
    write. This makes it a claim about what the process CAN do: ``StripeGateway.refund`` raises
    before it can build a request, so a copy-pasted line, a helpful future addition, or a fixture
    that reaches for the gateway cannot issue one against a real account.

    It also states the boundary where a reader will be standing when they wonder about it: refund is
    not skipped because it is hard, it is skipped because ==verifying it costs a real charge on a
    real card==, and that is not a thing a test may decide to do.
    """

    async def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "the live harness must never issue a refund: verifying that path requires a REAL "
            "charge to refund, which is money leaving a real card. GatewayOperation.REFUND stays "
            "unverified in `live_verifications()` until somebody decides to pay that price "
            "deliberately — it is not a decision a test gets to make."
        )

    monkeypatch.setattr(StripeGateway, "refund", _refuse, raising=True)


@pytest.fixture
def stripe_api(secret_key: str) -> Iterator[httpx.Client]:
    """A raw client for CONFIRMING what the gateway did, independently of the gateway.

    ==Deliberately not the gateway's own client.== Asking the code under test whether the code under
    test worked is the shape of a probe that agrees with itself. The session is created through
    ``StripeGateway`` — the real production object — and then looked up through a separate,
    hand-built request, so the confirmation comes from Stripe rather than from a return value.
    """
    with httpx.Client(
        base_url=STRIPE_API_BASE,
        headers={"Authorization": f"Bearer {secret_key}"},
        timeout=HTTP_TIMEOUT,
    ) as client:
        yield client


def test_the_control_proves_this_process_really_reaches_stripe(secret_key: str) -> None:
    """==The control, and it runs before anything is created.==

    A stub, a proxy, an offline cache or a mis-wired fixture can all return something that looks
    like success. None of them can produce Stripe's own ``401`` for a key Stripe never issued. So
    this asks for a rejection and requires it: if THIS passes, the requests below really left the
    machine and really arrived.

    A ``GET`` with a bogus key, so the control itself creates nothing at all — not even a session to
    clean up. The key below is a literal, and it is not a redaction of anything.

    .. note::

       ==``secret_key`` is taken and deliberately not used: that is the SKIP, not an oversight.==
       The marker only opens the network door; it skips nothing. Without this dependency this test
       had no reason to stop, so a plain offline ``pytest`` on a machine with no key at all still
       opened a TLS connection to api.stripe.com — measured, not theorised. ==The skip rides on the
       fixture==, exactly as ``test_network_guard`` records for the db suite: "the marker is not a
       guard".
    """
    del secret_key  # the control must use a key Stripe never issued; see the note above
    with httpx.Client(
        base_url=STRIPE_API_BASE,
        headers={"Authorization": "Bearer sk_test_THIS_KEY_WAS_NEVER_ISSUED_BY_STRIPE"},
        timeout=HTTP_TIMEOUT,
    ) as client:
        response = client.get("/checkout/sessions", params={"limit": 1})

    assert response.status_code == 401, (
        "a deliberately invalid key did not get Stripe's 401, so this process is NOT talking to "
        "the real api.stripe.com and no evidence gathered here would mean anything. Got "
        f"{response.status_code}."
    )
    body: dict[str, Any] = response.json()
    assert body.get("error", {}).get("type") == "invalid_request_error", body


async def test_the_gateway_opens_a_real_checkout_session_and_expires_it(
    secret_key: str, stripe_api: httpx.Client
) -> None:
    """==The exercise itself: the production ``StripeGateway``, the real API, and no money.==

    Create → read back independently → expire. Each step asserts something Stripe decided rather
    than something this code returned, and the run leaves the account as it found it: no open
    session, no charge, nothing pending.

    ==The idempotency key is fresh per run, and that is load-bearing.== Stripe replays a repeated
    key for 24 hours, so a fixed one would make the SECOND run hand back the first run's (by then
    expired) session without creating anything — a probe that passes while exercising nothing, which
    is the exact failure this harness exists to make impossible.
    """
    gateway = StripeGateway()  # no transport: the real one, exactly as production builds it
    expires_at = datetime.now(UTC) + gateway.checkout_session_floor + timedelta(minutes=5)

    session = await gateway.create_checkout_session(
        idempotency_key=f"live-verification:{uuid.uuid4()}",
        amount_cents=AMOUNT_CENTS,
        currency="usd",
        expires_at=expires_at,
        return_url=RETURN_URL,
        secrets={"secret_key": secret_key},
    )

    assert session.checkout_session_id.startswith("cs_"), session.checkout_session_id
    assert session.checkout_url.startswith("https://"), session.checkout_url

    # ==Confirmed by Stripe, not by the return value.== A separate request on a separate client; a
    # gateway that fabricated a plausible-looking response could not survive this line.
    read_back = stripe_api.get(f"/checkout/sessions/{session.checkout_session_id}")
    read_back.raise_for_status()
    opened: dict[str, Any] = read_back.json()
    assert opened["id"] == session.checkout_session_id
    assert opened["status"] == "open", opened["status"]
    assert opened["payment_status"] == "unpaid", opened["payment_status"]
    assert opened["amount_total"] == AMOUNT_CENTS
    assert opened["payment_intent"] is None, (
        "Stripe leaves the PaymentIntent null until a guest starts paying, and the arbiter's "
        "anchoring depends on it — so it is asserted here, where it can be observed against the "
        "real API instead of assumed from the documentation"
    )

    # ==Leave nothing standing.== An open live session is a payable invitation; expiring it is both
    # the cleanup and a second thing this run gets to observe the API doing.
    expired = stripe_api.post(f"/checkout/sessions/{session.checkout_session_id}/expire")
    expired.raise_for_status()
    closed: dict[str, Any] = expired.json()
    assert closed["status"] == "expired", closed["status"]

    mode = "LIVE" if opened["livemode"] else "TEST"
    print(
        "\n=== EVIDENCE for live_verifications(CredentialProvider.STRIPE) ===\n"
        "  operation   : checkout (GatewayOperation.CHECKOUT)\n"
        f"  verified_on : {datetime.now(UTC).date().isoformat()}\n"
        f"  mode        : {mode} (Stripe's own `livemode` on the session it created)\n"
        f"  session     : {opened['id']}\n"
        "  observed    : created via StripeGateway.create_checkout_session against api.stripe.com; "
        "read back independently as status=open, payment_status=unpaid, payment_intent=null; "
        "expired to status=expired. No charge, no refund, no money moved.\n"
        "  NOT verified: refund — it needs a real charge to refund, and was never called.\n"
        "==================================================================\n"
    )
