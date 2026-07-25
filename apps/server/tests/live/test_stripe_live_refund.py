"""Verify ``StripeGateway.refund`` against the real API. ==This moves REAL MONEY: $1, returned.==

Everything else in this directory is free. This is not, and it cannot be made so.

.. rubric:: ==Why it must be two phases: in live mode there are no test cards==

Stripe's test cards (``4242…``) exist only in test mode. A LIVE Checkout Session is completed by a
**person, with a real card** — there is no API call that pays it, and none that mints a live charge
out of nothing. So the charge that ``refund`` needs cannot be created by this harness at all, and
pretending otherwise is exactly how one ends up with a refund test that silently verifies nothing.

The run is therefore cut where the human is:

* **Phase A** — :func:`test_phase_a_opens_a_payable_one_dollar_session`. Opens a live $1 Checkout
  Session and prints its URL. ==It deliberately does NOT expire it==: the whole point is that a
  human can pay it. That is the one place in this repository where something is left standing on
  purpose, and it is why phase A needs its own opt-in;
* **Phase B** — :func:`test_phase_b_refunds_the_real_charge_through_the_gateway`. Given the paid
  session, it runs the REAL ``StripeGateway.refund`` — the call unplugged everywhere else — and
  confirms the money went back by a separate request to Stripe.

.. rubric:: The barriers, and why each one exists

* ==**the hard cap is the absence of a knob.**== ``open_one_dollar_session`` takes no amount. A
  units bug (``100`` read as dollars) charges **$100** instead of $1, and that mistake arrives by
  inattention, never by decision — so there is nothing to mistype;
* ==**phase A's idempotency key is FIXED, and phase B's is production's own.**== The fixed key is
  the exact opposite of the zero-cost checkout harness, for the exact opposite reason: there, a
  repeated key would replay an old session and exercise nothing; here, a fresh key would mint a
  **second payable $1 invitation**. A re-run of phase A inside Stripe's 24-hour window returns the
  same session, so a retry cannot charge twice — and when that replayed session turns out to have
  lapsed or already been paid, phase A says which, and names the run id that opens a new one;
* ==**every charge is refunded in a ``finally``, through the INDEPENDENT client.**== Phase B exists
  because the gateway's refund has never run for real. If it is broken, the cleanup must not share
  its fault — so the safety net does not go through ``StripeGateway`` at all;
* ==**if the refund cannot be completed, the run SHOUTS the charge id.**== $1 stuck on somebody's
  card behind a green test is the worst thing this file can produce, because nobody goes looking;
* ==**phase B refunds ONLY what phase A opened.**== The session is marked at creation (the return
  URL carries the harness's name and the run id, and Stripe echoes it back), and phase B demands
  that mark before it resolves the PaymentIntent. This account holds **real customer invoices**, and
  a $1 amount identifies nothing — so a mistyped or hostile session id must not reach a refund.

.. rubric:: How to run it

.. code-block:: bash

   read -rs AETHERCAL_LIVE_STRIPE_SECRET_KEY && export AETHERCAL_LIVE_STRIPE_SECRET_KEY

   # Phase A — opens a payable $1 session and prints its URL. Requires the explicit opt-in,
   # because it leaves a live invitation standing.
   AETHERCAL_LIVE_STRIPE_ALLOW_REAL_CHARGE=1 \
     uv run pytest apps/server/tests/live/test_stripe_live_refund.py -m live_provider -s

   # ...pay it with a real card, then:

   # Phase B — refunds it through the real gateway and verifies with a separate request.
   AETHERCAL_LIVE_STRIPE_CHECKOUT_SESSION=cs_live_… \
     uv run pytest apps/server/tests/live/test_stripe_live_refund.py -m live_provider -s

Each phase skips unless its own variable is set, so neither can run by surprise — and neither is
part of an ordinary ``-m live_provider`` run.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.stripe import StripeGateway
from aethercal.server.services.outbox import refund_dedupe_key

pytestmark = pytest.mark.live_provider

ALLOW_REAL_CHARGE_ENV = "AETHERCAL_LIVE_STRIPE_ALLOW_REAL_CHARGE"
PAID_SESSION_ENV = "AETHERCAL_LIVE_STRIPE_CHECKOUT_SESSION"

RUN_ID_ENV = "AETHERCAL_LIVE_STRIPE_RUN_ID"
DEFAULT_RUN_ID = "v1"
PHASE_A_PURPOSE = "refund"
"""The provenance segment phase A stamps. Phase B demands it; the checkout harness uses another."""


def _run_id() -> str:
    """Which phase-A run this is. ==One reading, used by the key, the mark and the demand.==

    Phase A stamps it into the session and phase B requires the same value back, so a session opened
    under one run id is not refunded by a phase B expecting another. Two readings of the environment
    could disagree; one cannot.
    """
    return os.environ.get(RUN_ID_ENV, "").strip() or DEFAULT_RUN_ID


def _phase_a_idempotency_key() -> str:
    """==FIXED on purpose.== A re-run inside Stripe's 24-hour window returns the SAME session.

    A fresh key here would mint a second payable $1 invitation every time somebody re-ran phase A —
    two invitations, two possible charges, from a command that reads like a retry. The zero-cost
    checkout harness needs the exact opposite (a fresh key, or it replays an old session and
    exercises nothing), and the two live one directory apart, so the reasoning is written in both.

    ==The escape hatch is deliberate, and it is named where it is needed.== A fixed key has a real
    cost: if the session lapses unpaid, re-running phase A replays the *expired* one and there is no
    way to open another for 24 hours. So the run id is a variable — export
    ``AETHERCAL_LIVE_STRIPE_RUN_ID=v2`` to open a new payable session on purpose. It cannot happen
    by accident (it is not the default, and phase A already carries its own opt-in), and phase A
    tells the operator this exact sentence at the moment it hits the wall.
    """
    return f"aethercal-live-refund-verification:{_run_id()}"


HUMAN_PAYMENT_WINDOW = timedelta(hours=2)
"""How long the phase-A session stays payable. Comfortably over Stripe's 30-minute floor and well
under its 24-hour ceiling — long enough for a person to find their wallet, short enough that a
forgotten session lapses on its own."""


@pytest.fixture
def real_charge_allowed() -> None:
    """==Phase A's opt-in.== Without it, nothing that leaves a payable session standing may run.

    Every other test in this directory cleans up after itself; phase A's entire purpose is not to,
    so that a human can pay what it opened. That inversion deserves a deliberate act rather than
    inheriting the key's permission — otherwise an ordinary ``-m live_provider`` run would litter a
    real account with live invitations to pay.
    """
    if os.environ.get(ALLOW_REAL_CHARGE_ENV, "").strip() != "1":
        pytest.skip(
            f"{ALLOW_REAL_CHARGE_ENV}=1 is not set. Phase A opens a LIVE, payable $1 Checkout "
            "Session and deliberately leaves it open for a human to pay, so it does not run on the "
            "strength of the API key alone."
        )


@pytest.fixture
def paid_session_id() -> str:
    """==Phase B's input:== the Checkout Session a human actually paid, from phase A's output."""
    value = os.environ.get(PAID_SESSION_ENV, "").strip()
    if not value:
        pytest.skip(
            f"{PAID_SESSION_ENV} is not set. Phase B needs the Checkout Session a human has "
            "already paid (phase A prints its id and URL). In live mode there are no test cards, "
            "so the charge cannot be created from here."
        )
    return value


async def test_phase_a_opens_a_payable_one_dollar_session(  # noqa: PLR0913
    real_charge_allowed: None,
    open_one_dollar_session: Callable[..., Any],
    one_dollar_cents: int,
    one_dollar_currency: str,
    stripe_api: httpx.Client,
    expire_session: Callable[[str], str | None],
    harness_return_url: Callable[[str], str],
) -> None:
    """==Phase A: open a real, payable $1 invitation and stop.== No money moves in this test.

    The session is left OPEN — the single deliberate exception to this directory's clean-up-always
    rule, and the reason phase A carries its own opt-in. Nothing is charged until a person pays it.

    What is asserted is what Stripe says about the thing that was created, above all ==the amount==:
    if the cap were ever wrong, this is the last moment before a human can pay it, and it is checked
    against the fixture rather than a literal retyped here.
    """
    expires_at = datetime.now(UTC) + HUMAN_PAYMENT_WINDOW
    session = await open_one_dollar_session(
        idempotency_key=_phase_a_idempotency_key(),
        expires_at=expires_at,
        # ==The provenance mark, applied AT CREATION through the production path.== Phase B refunds
        # only what carries it: this account holds real customer payments and $1 identifies nothing.
        return_url=harness_return_url(f"{PHASE_A_PURPOSE}/{_run_id()}"),
    )

    # ==The invitation exists from the line above onwards, so from here the default is CLOSE IT.==
    # The cap used to be checked after creation with nothing to catch a failure, so a session that
    # failed validation — the wrong amount, say — stayed OPEN and PAYABLE. The one case where a
    # failure is punished by leaving somebody able to pay.
    must_expire = True
    try:
        opened_response = stripe_api.get(f"/checkout/sessions/{session.checkout_session_id}")
        opened_response.raise_for_status()
        opened: dict[str, Any] = opened_response.json()

        # ==The fixed idempotency key's one real cost, surfaced instead of suffered.== Stripe
        # replays a repeated key for 24 hours, so this may be a session an earlier run opened. If it
        # has since lapsed — or has already been paid — the operator needs to be told WHICH, and
        # what to do, rather than reading a bare `status != 'open'` assertion and guessing.
        if opened.get("status") != "open":
            must_expire = False  # already expired, or PAID: either way not ours to close
            already_paid = opened.get("payment_status") == "paid"
            pytest.fail(
                f"the phase-A session for run id "
                f"{_run_id()!r} is "
                f"{opened.get('status')!r}, not open — Stripe replayed the one an earlier run "
                f"created (idempotency keys live 24 hours).\n\n"
                + (
                    f"It was PAID: {opened['id']}. Nothing new is needed — run phase B on it:\n"
                    f"  AETHERCAL_LIVE_STRIPE_CHECKOUT_SESSION={opened['id']} \\\n"
                    "    uv run pytest apps/server/tests/live/test_stripe_live_refund.py "
                    "-m live_provider -s\n"
                    if already_paid
                    else "It lapsed unpaid, so there is nothing to refund and no way to reopen it. "
                    "To open a NEW payable session deliberately, pick a fresh run id:\n"
                    f"  {RUN_ID_ENV}=v2 AETHERCAL_LIVE_STRIPE_ALLOW_REAL_CHARGE=1 \\\n"
                    "    uv run pytest apps/server/tests/live/test_stripe_live_refund.py "
                    "-m live_provider -s\n"
                )
            )

        assert opened["amount_total"] == one_dollar_cents, (
            f"Stripe says this session is for {opened['amount_total']} and the hard cap is "
            f"{one_dollar_cents}. Expiring it rather than letting anybody pay it."
        )
        assert opened["currency"] == one_dollar_currency, opened["currency"]
        assert opened["payment_status"] == "unpaid", opened["payment_status"]
        assert isinstance(opened.get("livemode"), bool), (
            "Stripe did not say which MODE this session is in "
            f"(livemode={opened.get('livemode')!r}), and the mode is what decides whether the "
            "evidence is worth a live credential"
        )

        # ==The URL comes from the SOURCE, not from the gateway's return value.== A human opens
        # this and types a card into it; if the gateway ever returned a URL that was not the one
        # Stripe has for this session, printing its answer would send somebody to pay somewhere
        # else. So the printed URL is Stripe's, and the gateway's is checked AGAINST it.
        checkout_url = opened.get("url")
        assert isinstance(checkout_url, str) and checkout_url.startswith("https://"), checkout_url
        assert checkout_url == session.checkout_url, (
            "the gateway returned a different checkout URL from the one Stripe holds for this "
            "session; refusing to publish either"
        )

        must_expire = False  # ==validated: leaving it open is now the POINT of this phase==
        mode = "LIVE" if opened["livemode"] else "TEST"
        print(
            "\n=== PHASE A: a payable session is now OPEN ===\n"
            f"  mode        : {mode}\n"
            f"  amount      : {opened['amount_total']} cents {opened['currency'].upper()} "
            "(the hard cap; nothing else can be created here)\n"
            f"  session     : {opened['id']}\n"
            f"  expires     : {datetime.fromtimestamp(opened['expires_at'], UTC).isoformat()}\n"
            f"  PAY IT HERE : {checkout_url}\n"
            "\n"
            f"  {'REAL MONEY. ' if opened['livemode'] else ''}Paying this charges a real card "
            f"{opened['amount_total']} cents. Phase B refunds it.\n"
            "\n"
            "  Then run phase B:\n"
            f"    AETHERCAL_LIVE_STRIPE_CHECKOUT_SESSION={opened['id']} \\\n"
            "      uv run pytest apps/server/tests/live/test_stripe_live_refund.py "
            "-m live_provider -s\n"
            "\n"
            "  Changed your mind? Expire it from the dashboard and nothing happens.\n"
            "==============================================\n"
        )
    finally:
        if must_expire:
            # Validation failed, or the read did. ==Nothing unvalidated is left payable.==
            problem = expire_session(session.checkout_session_id)
            print(
                f"\n!!! phase A created {session.checkout_session_id} and did NOT validate it, so "
                "it was expired rather than published.\n"
                + (
                    f"    THE EXPIRY ALSO FAILED: {problem}\n"
                    "    A live, payable session may still be OPEN — expire it by hand NOW.\n"
                    if problem is not None
                    else "    It is expired; nobody can pay it.\n"
                )
            )


async def test_phase_b_refunds_the_real_charge_through_the_gateway(  # noqa: PLR0913
    paid_session_id: str,
    refund_reconnected: None,
    secret_key: str,
    gateway: StripeGateway,
    one_dollar_cents: int,
    one_dollar_currency: str,
    terminal_refund_success: str,
    stripe_api: httpx.Client,
    require_phase_a_provenance: Callable[[Mapping[str, Any], str], None],
    ensure_refunded: Callable[[str, str], str | None],
    shout_that_money_is_held: Callable[[str, str], None],
) -> None:
    """==Phase B: the real ``StripeGateway.refund``, against a real charge.== The money goes back.

    This is the ONLY test permitted to call it (``refund_reconnected`` says so in the signature),
    and the only evidence that can ever justify recording ``GatewayOperation.REFUND`` as verified.

    .. rubric:: The order of the barriers

    1. resolve the session and require ``payment_status == "paid"`` — refunding an unpaid session is
       a no-op that would look like success;
    2. re-check the amount against the hard cap **before** touching anything: this is money coming
       back, so a mismatch means we are looking at the wrong charge entirely;
    3. run the gateway's refund on ==production's own idempotency key== (:func:`refund_dedupe_key`,
       imported rather than re-typed), so what is verified is the key the refund runner really
       sends — a retry gets the same refund and never a second one;
    4. confirm with a SEPARATE request. The gateway returning ``None`` is not evidence; Stripe
       saying ``succeeded`` is;
    5. and in the ``finally``, guarantee the money is back **through the independent client** —
       because the thing on trial here is precisely the code that might have failed to send it.
    """
    session_response = stripe_api.get(f"/checkout/sessions/{paid_session_id}")
    session_response.raise_for_status()
    session: dict[str, Any] = session_response.json()

    # ==PROVENANCE FIRST, before anything is read as a target.== Nothing about a session id proves
    # it came from phase A, and this account carries real customer invoices — so a mistyped, stale
    # or hostile value would have pointed the refund at somebody else's transaction. The amount is
    # no defence: $1 is one figure among thousands. This runs before the PaymentIntent is even
    # resolved, so a foreign session is refused before it can become a target at all.
    require_phase_a_provenance(session, _run_id())

    assert session["currency"] == one_dollar_currency, (
        f"session {paid_session_id} is in {session['currency']!r}, not {one_dollar_currency!r}"
    )
    assert session["payment_status"] == "paid", (
        f"session {paid_session_id} is {session['payment_status']!r}, not 'paid'. Phase B refunds "
        "a charge that exists; in live mode nothing here can create one, so pay the phase-A "
        "session first."
    )
    assert session["amount_total"] == one_dollar_cents, (
        f"this session is for {session['amount_total']} cents and the hard cap is "
        f"{one_dollar_cents}. Refusing to act on a charge this harness did not open."
    )
    payment_intent_id = session["payment_intent"]
    assert isinstance(payment_intent_id, str) and payment_intent_id.startswith("pi_"), (
        f"expected a PaymentIntent id on the paid session, got {payment_intent_id!r}"
    )

    # ==Production's own key, imported.== `refund_dedupe_key` is what `make_refund_runner` sends, so
    # a crash between the provider call and our commit re-sends THIS key and Stripe returns the same
    # refund rather than a second one. Re-typing its shape here would verify a lookalike.
    idempotency_key = refund_dedupe_key(payment_intent_id)

    failed = False
    try:
        await gateway.refund(
            provider_ref=payment_intent_id,
            idempotency_key=idempotency_key,
            secrets={"secret_key": secret_key},
        )

        # ==Settle FIRST, certify second.== `ensure_refunded` is the only thing here that knows
        # whether the money is actually back: it reads the capture, counts only terminal successes,
        # and waits out anything in flight. Until it says `None`, nothing is known — so it runs
        # before a single line of evidence is composed.
        #
        # The bug this ordering closes: the evidence block used to accept `pending` and then print
        # "The money went back". ==The cleanup had been taught that pending is not done, and the
        # thing PRINTING THE CERTIFICATE had not.== That block is what gets pasted into
        # `live_verifications()`, so a lie here becomes a lie in the register — the exact failure
        # this whole change exists to prevent, one layer up from where it was fixed.
        unsettled = ensure_refunded(payment_intent_id, idempotency_key)
        assert unsettled is None, unsettled

        # ==Now re-read from the source and require TERMINAL success.== Not the value returned by
        # anything above: a fresh answer from Stripe, after the settling is done.
        refunds_response = stripe_api.get(
            "/refunds", params={"payment_intent": payment_intent_id, "limit": 10}
        )
        refunds_response.raise_for_status()
        refunds: list[dict[str, Any]] = refunds_response.json().get("data", [])
        assert len(refunds) == 1, (
            f"expected exactly ONE refund for {payment_intent_id}, found {len(refunds)}. More than "
            "one means the gateway did not do the job alone — the cleanup topped it up — so "
            "GatewayOperation.REFUND is NOT verified by this run, whatever the money did."
        )
        assert refunds[0]["status"] == terminal_refund_success, (
            f"the refund is {refunds[0]['status']!r}, not {terminal_refund_success!r}. Only a "
            "terminal success may be certified: a pending refund can still fail, and evidence that "
            "says otherwise would put a false record in live_verifications()."
        )
        assert refunds[0]["amount"] == one_dollar_cents, refunds[0]["amount"]

        intent_response = stripe_api.get(f"/payment_intents/{payment_intent_id}")
        intent_response.raise_for_status()
        charge_id = str(intent_response.json().get("latest_charge") or "")

        mode = "LIVE" if session["livemode"] else "TEST"
        print(
            "\n=== EVIDENCE for live_verifications(CredentialProvider.STRIPE) ===\n"
            "  operation   : GatewayOperation.REFUND\n"
            f"  mode        : ProviderMode.{mode}  <- Stripe's own `livemode`, on the paid "
            "session.\n"
            "                ==Only ProviderMode.LIVE authorises a live credential.==\n"
            f"  verified_on : {datetime.now(UTC).date().isoformat()}\n"
            f"  session     : {paid_session_id}\n"
            f"  intent      : {payment_intent_id}\n"
            f"  charge      : {charge_id}\n"
            f"  refund      : {refunds[0]['id']} status={refunds[0]['status']} "
            f"amount={refunds[0]['amount']}\n"
            "  observed    : a real card was charged by a human; StripeGateway.refund was called "
            "against api.stripe.com on production's own idempotency key; after the refund reached "
            "a TERMINAL state, a SEPARATE request confirmed exactly one refund, status=succeeded, "
            "for the full captured amount. The money went back.\n"
            "==================================================================\n"
        )
    except BaseException:
        failed = True
        raise
    finally:
        # ==The money comes back even if everything above went wrong.== Through the independent
        # client, because the gateway is the thing on trial here.
        problem = ensure_refunded(payment_intent_id, idempotency_key)
        if problem is not None:
            shout_that_money_is_held(payment_intent_id, problem)
            if not failed:
                # Nothing is propagating, so the stuck money IS the result. If the body had already
                # failed, the shout above is the alarm and ==the original exception travels intact==
                # — a cleanup error must never replace the failure that explains it.
                raise AssertionError(problem)
