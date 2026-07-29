"""Exercise the REAL ``StripeGateway`` checkout against the REAL Stripe API. ==Zero money moves.==

.. rubric:: What this is for

``services/tenant_credentials.live_verifications()`` refuses a live payment credential until
somebody has actually run the gateway against the provider. ==This is the run for
``GatewayOperation.CHECKOUT``==, and what it prints is the evidence that goes with it.

.. rubric:: ==Zero cost, and that is a property of the CALLS, not of an intention==

Creating a Checkout Session moves no money: a session is an *invitation* to pay, and nobody pays
this one. It is created, read back, and expired — the last so no live invitation is left standing.
==No card, no charge, no refund, no payout.== ``StripeGateway.refund`` is unplugged for this whole
directory (see ``conftest``), so the money-moving call is not merely unused here; it is unreachable.

The REFUND operation has its own harness — ``test_stripe_live_refund.py`` — because verifying it
costs a real charge. That is a different decision with a different price, and it lives in a
different file so nobody wanders into it.

.. rubric:: How to run it

.. code-block:: bash

   read -rs AETHERCAL_LIVE_STRIPE_SECRET_KEY && export AETHERCAL_LIVE_STRIPE_SECRET_KEY
   uv run pytest apps/server/tests/live/test_stripe_live_checkout.py -m live_provider -s

``-s`` because the evidence is printed and pytest swallows the output of passing tests. Both
``sk_test_`` and ``sk_live_`` keys work, and the run reports which one it used: a test-mode run
proves the code path end to end but says nothing about live mode, so ``livemode`` is REPORTED rather
than assumed — whoever writes the register decides what that evidence supports.

.. rubric:: ==Every probe carries a control, and the control is a FIXTURE==

A "live" test that quietly reached a stub, a proxy, or nothing at all would produce evidence for
code that never ran — the worst possible failure here, because the evidence is the entire point. So
``conftest.stripe_reachable`` runs first with a deliberately invalid key and requires Stripe's own
``401``. Nothing local produces that.

==It is a fixture, not a neighbouring test, and that is the fix for a real hole==: running the
evidence-producing test BY NAME left the control uncollected, so a record could be written into
``live_verifications()`` with nothing having shown the process reaches Stripe at all. A dependency
cannot be selected around.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.money import implementation_fingerprint
from aethercal.server.integrations.stripe import StripeGateway
from aethercal.server.services.tenant_credentials import CredentialProvider, GatewayOperation

CHECKOUT = GatewayOperation.CHECKOUT

pytestmark = pytest.mark.live_provider


def test_the_control_proves_this_process_really_reaches_stripe(stripe_reachable: int) -> None:
    """==The control, REPORTED as a test of its own — but ENFORCED as a fixture.==

    The work moved to :func:`~conftest.stripe_reachable`, and that move is the point. This function
    used to BE the control, standing beside the run it vouched for — and a sibling is not a
    precondition: ``pytest <file>::test_the_gateway_opens…`` collects the evidence run and leaves
    the control behind, so a record could reach ``live_verifications()`` without anybody having
    shown that this process reaches Stripe at all.

    It stays as a named test because a control visible only as a fixture is a control nobody reads
    in the report. What it asserts now is what the fixture observed.
    """
    assert stripe_reachable == 401, (
        "the control did not get Stripe's own 401 for a key Stripe never issued, so this process "
        f"is not talking to the real api.stripe.com. Got {stripe_reachable}."
    )


async def test_the_gateway_opens_a_real_checkout_session_and_expires_it(  # noqa: PLR0913
    stripe_reachable: int,
    gateway: StripeGateway,
    open_one_dollar_session: Callable[..., Any],
    one_dollar_cents: int,
    stripe_api: httpx.Client,
    expire_session: Callable[[str], str | None],
    harness_return_url: Callable[[str], str],
) -> None:
    """==The exercise itself: the production ``StripeGateway``, the real API, and no money.==

    Create → read back independently → expire. Each step asserts something Stripe decided rather
    than something this code returned, and the run leaves the account as it found it.

    ==The idempotency key is fresh per run, and that is load-bearing HERE and inverted in phase A.==
    Stripe replays a repeated key for 24 hours, so a fixed one would make the *second* run hand back
    the first run's (by then expired) session without creating anything — a probe that passes while
    exercising nothing. Phase A wants the opposite, for the opposite reason: there, a fresh key
    would mint a second *payable* invitation.

    .. rubric:: ==Why the expiry moved into a ``finally``==

    It used to be the last statement of the body, so any assertion above it left a LIVE, payable
    Checkout Session standing in a real account — a failure whose punishment was an open invitation
    to pay. Cleanup now runs whatever the body did, and it is careful in both directions: it never
    replaces the exception on its way out, and it never lets a cleanup failure pass silently.
    """
    expires_at = datetime.now(UTC) + gateway.checkout_session_floor + timedelta(minutes=5)
    session = await open_one_dollar_session(
        idempotency_key=f"live-verification:{uuid.uuid4()}",
        expires_at=expires_at,
        # Marked as the ZERO-COST harness's, so it can never be mistaken for a payable phase-A
        # session — and so phase B refuses it if anybody ever pastes this id there.
        return_url=harness_return_url("checkout"),
    )

    failed = False
    must_expire = True
    try:
        assert session.checkout_session_id.startswith("cs_"), session.checkout_session_id
        assert session.checkout_url.startswith("https://"), session.checkout_url

        # ==Confirmed by Stripe, not by the return value.== A separate request on a separate client;
        # a gateway that fabricated a plausible-looking response could not survive this line.
        read_back = stripe_api.get(f"/checkout/sessions/{session.checkout_session_id}")
        read_back.raise_for_status()
        opened: dict[str, Any] = read_back.json()
        assert opened["id"] == session.checkout_session_id
        assert opened["status"] == "open", opened["status"]
        assert opened["payment_status"] == "unpaid", opened["payment_status"]
        assert opened["amount_total"] == one_dollar_cents
        assert opened["payment_intent"] is None, (
            "Stripe leaves the PaymentIntent null until a guest starts paying, and the arbiter's "
            "anchoring depends on it — so it is asserted here, where it can be observed against "
            "the real API instead of assumed from the documentation"
        )

        assert isinstance(opened.get("livemode"), bool), opened.get("livemode")

        # ==Expire it HERE, and only then certify it.== The evidence used to say "expired
        # afterwards" while the expiry was still sitting in the `finally` BELOW the print — an
        # observation about the future, written as though it had been made. If that expiry then
        # failed, the block already claimed otherwise, and that block is what gets pasted into
        # `live_verifications()`. Same defect as the refund evidence accepting `pending`: what
        # certifies must be what measured. `expire_session` verifies `status == "expired"` against
        # Stripe, so passing it is the observation.
        problem = expire_session(session.checkout_session_id)
        assert problem is None, problem
        must_expire = False

        mode = "LIVE" if opened["livemode"] else "TEST"
        print(
            "\n=== EVIDENCE for live_verifications(CredentialProvider.STRIPE) ===\n"
            "  operation   : GatewayOperation.CHECKOUT\n"
            f"  implement.  : {implementation_fingerprint(CredentialProvider.STRIPE, CHECKOUT)}\n"
            "                ==The code this run exercised.== Editing that method invalidates this "
            "record and demands a fresh run: a verification is about an implementation, not about "
            "a provider's name.\n"
            f"  mode        : ProviderMode.{mode}  <- Stripe's own `livemode`, on the session it "
            "created.\n"
            "                ==Only ProviderMode.LIVE authorises a live credential.== A TEST run "
            "is honest evidence about the transport and buys nothing the guard will spend.\n"
            f"  verified_on : {datetime.now(UTC).date().isoformat()}\n"
            f"  session     : {opened['id']}\n"
            "  observed    : created via StripeGateway.create_checkout_session against "
            "api.stripe.com; read back independently as status=open, payment_status=unpaid, "
            "payment_intent=null; then expired and confirmed status=expired. No charge, no "
            "refund, no money moved.\n"
            "  NOT verified: refund — see test_stripe_live_refund.py.\n"
            "==================================================================\n"
        )
    except BaseException:
        failed = True
        raise
    finally:
        # ==The backstop for the failure path only.== The happy path already expired it above and
        # checked the result; re-expiring an expired session would report a spurious problem.
        problem = expire_session(session.checkout_session_id) if must_expire else None
        if problem is not None:
            print(
                f"\n!!! a LIVE Checkout Session may still be open: {problem}\n"
                f"    expire it by hand: {session.checkout_session_id}\n"
            )
            if not failed:
                # Nothing is propagating, so this cleanup failure IS the result. When the body has
                # already failed we shout instead — ==the original exception is the useful one==.
                raise AssertionError(problem)
