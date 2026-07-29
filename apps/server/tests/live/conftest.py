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

.. rubric:: ==Every probe carries its control, and the control is a FIXTURE==

:func:`stripe_reachable` demands Stripe's own ``401`` for a key Stripe never issued — the one answer
no stub, proxy or offline cache can fabricate. It lives here rather than as a test beside the runs
it vouches for, because a sibling test is not a precondition: running the evidence-producing test by
name would simply leave the control uncollected, and the record it prints goes into the register the
money guard reads.

.. rubric:: ==Cleanup never masks the failure it was cleaning up after==

:func:`ensure_refunded` and :func:`expire_session` are written to be called from a ``finally`` and
==never raise==. They report a problem as a string, and the caller decides: if the body already
failed, the problem is SHOUTED and the original exception travels intact; if the body passed, the
cleanup problem becomes the failure. Money stuck behind a green test is the worst outcome this
harness can produce, and a cleanup error that swallows a real one is the second worst.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import time
from collections.abc import Callable, Coroutine, Iterator, Mapping
from datetime import datetime
from typing import Any

import httpx
import pytest

from aethercal.server.integrations.stripe import (
    REFUND_SUCCEEDED,
    TERMINAL_REFUND_FAILURES,
    StripeGateway,
)
from aethercal.server.services.payments import CheckoutSession

SECRET_KEY_ENV = "AETHERCAL_LIVE_STRIPE_SECRET_KEY"
STRIPE_API_BASE = "https://api.stripe.com/v1"
DASHBOARD_PAYMENT_URL = "https://dashboard.stripe.com/payments"
DASHBOARD_SESSIONS_URL = "https://dashboard.stripe.com/payments?type=checkout_session"
HTTP_TIMEOUT = httpx.Timeout(30.0)

ONE_DOLLAR_CENTS = 100
"""==The hard cap.== The only amount this harness may ever create, in the currency's minor unit."""

CURRENCY = "usd"
"""Fixed alongside the cap: 100 minor units is $1.00 in USD and something else elsewhere."""

PROVENANCE_BASE = "https://example.com/aethercal-live-verification"
"""==Where the harness signs its work.== Stripe requires a well-formed return URL, never fetches it,
and echoes it back on the session as ``success_url`` — so it is the one field this harness can write
at creation and read back later. See :func:`harness_return_url`."""

PROVENANCE_SECRET_ENV = "AETHERCAL_LIVE_STRIPE_PROVENANCE_SECRET"
"""==The HMAC key that makes the mark unforgeable.== From the ENVIRONMENT, never this repository — a
signing key committed beside the thing it signs proves nothing at all. Absent, the money harness
REFUSES TO RUN (:func:`provenance_secret`); it never degrades to an unauthenticated mark."""

STATE_DIR_ENV = "AETHERCAL_LIVE_STRIPE_STATE_DIR"
"""Where phase A writes what phase B must know. Defaults under ``~/.aethercal``: ==outside the
repository==, because it holds the run's nonce."""

DEFAULT_STATE_DIR = pathlib.Path.home() / ".aethercal" / "live-stripe"

PHASE_A_PURPOSE = "refund"
"""The segment marking a PAYABLE phase-A session, so a free checkout-harness session (marked
``checkout``) can never be mistaken for one."""

RUN_ID_ENV = "AETHERCAL_LIVE_STRIPE_RUN_ID"
"""Names the phase-A run. It is part of the mark, so phase B can demand the exact run."""

CHECKOUT_PURPOSE = "checkout"
"""The zero-cost harness's segment. Nothing payable is ever left standing under it."""

"""==The ONLY status that means the money is back.== Imported from the adapter beside
:data:`REFUND_DEAD_ENDS`, for the same reason: the gateway is the code that reads Stripe's
vocabulary off the API, and a second spelling here would be free to drift from the one production
acts on."""

REFUND_DEAD_ENDS = TERMINAL_REFUND_FAILURES
"""Terminal and the money did NOT move — so these count as zero, never as progress.

==Imported from the adapter, not spelled again.== The gateway decides what Stripe's terminal
failures are (it is the code that reads them off the API); a second copy here would be free to
drift from the one production acts on, and the drift would be invisible — both spellings look
right.

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
def one_dollar_currency() -> str:
    """The currency the cap is denominated in. ==Nothing in this directory re-types ``"usd"``.==

    100 minor units is $1.00 here and a different sum elsewhere, so the figure and the currency are
    one fact and are read from one place.
    """
    _assert_the_hard_cap()
    return CURRENCY


@pytest.fixture
def terminal_refund_success() -> str:
    """The ONE refund status that means the money is back. ==Read, never retyped.==

    A test that spelled ``"succeeded"`` itself would be a second definition of "done", free to drift
    from the one :func:`ensure_refunded` enforces — and the drift would be invisible, because both
    spellings look right.
    """
    return REFUND_SUCCEEDED


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
def stripe_reachable(secret_key: str, unauthenticated_stripe_api: httpx.Client) -> int:
    """==The control every probe in this directory carries.== Returns the status Stripe gave.

    .. rubric:: Why it is a FIXTURE and no longer a test standing beside one

    It was ``test_the_control_proves_this_process_really_reaches_stripe``, a sibling of the run it
    was supposed to vouch for — and a sibling is not a precondition. ``pytest <file>::<test>`` runs
    the evidence-producing test alone, and pytest promises no ordering even when both are collected.
    So the run that ==writes a record into the money guard's register== could happen without the one
    thing proving the process reached Stripe at all. Evidence produced without its control is not
    weak evidence; it is a claim about a round-trip nobody watched happen.

    A fixture cannot be skipped past. Anything that asks for it gets the control first, by
    construction — and ``tests/live/test_live_harness_guardrails.py`` asserts that every test in
    every provider-touching module asks.

    .. rubric:: What it proves, and why only a 401 proves it

    A stub, a proxy, an offline cache or a mis-wired fixture can all return something that looks
    like success. ==None of them can produce Stripe's own ``401`` for a key Stripe never issued.==
    So the control asks for a REJECTION and requires it: the one answer nothing local fabricates.

    A ``GET``, with a key Stripe never issued, so the control creates nothing — not even a session
    to clean up — and costs nothing.

    ==``secret_key`` is taken and deliberately not used: that is the SKIP, not an oversight.== The
    marker only opens the network door; it skips nothing. Depending on the real key here means every
    test that asks for this control also inherits the skip on a machine that has none — the lesson
    ``test_network_guard`` records as "the marker is not a guard", which a control living in its own
    function had to remember for itself.
    """
    del secret_key  # the control must use a key Stripe never issued; see above for why it is here

    response = unauthenticated_stripe_api.get("/checkout/sessions", params={"limit": 1})

    if response.status_code != 401:
        pytest.fail(
            "a deliberately invalid key did not get Stripe's 401, so this process is NOT talking "
            "to the real api.stripe.com and no evidence gathered here would mean anything. Got "
            f"{response.status_code}."
        )
    body: dict[str, Any] = response.json()
    if body.get("error", {}).get("type") != "invalid_request_error":
        pytest.fail(
            "the 401 did not carry Stripe's own error shape, so something between this process and "
            f"Stripe is answering for it: {body}"
        )
    return response.status_code


def _state_path(run_id: str) -> pathlib.Path:
    """The file holding one phase-A run's nonce and the session it opened."""
    directory = pathlib.Path(os.environ.get(STATE_DIR_ENV, "").strip() or DEFAULT_STATE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"phase-a-{run_id}.json"


def _read_state(run_id: str) -> dict[str, Any]:
    """What phase A wrote for this run, or ``{}``. ==Never raises==: a missing file is an answer."""
    try:
        loaded = json.loads(_state_path(run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_state(run_id: str, **fields: str) -> None:
    """Merge ``fields`` into this run's record, owner-readable only."""
    path = _state_path(run_id)
    state = _read_state(run_id) | fields
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    # Best effort: Windows and exotic filesystems have no POSIX mode to set, and a state file
    # nobody could tighten is still better than no state file at all.
    with contextlib.suppress(OSError):  # pragma: no cover - platform dependent
        path.chmod(0o600)


def _mark_for(run_id: str, *, nonce: str, secret: str) -> str:
    """The authenticated mark for one phase-A run. ==An HMAC, not a public constant.==

    Covers the purpose and the run id, so a checkout-harness session or another run's session cannot
    present a mark that verifies here; and the NONCE, so the digest cannot be recomputed by anybody
    lacking both the key and this run's local state.
    """
    payload = f"{PHASE_A_PURPOSE}/{run_id}/{nonce}".encode()
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@pytest.fixture
def provenance_secret(secret_key: str) -> str:
    """The HMAC key, from the environment. ==Absent, the harness REFUSES TO RUN.==

    ``fail``, not ``skip``, and the difference is the whole point: a skip would let somebody run the
    money harness with the provenance check quietly disabled — the silent no-op, aimed at the one
    barrier standing between our $1 and the real customer invoices in the same account. There is no
    unauthenticated mode to degrade to.

    It depends on :func:`secret_key` so the ORDER is right: a machine with no API key skips the
    whole suite quietly (nothing was being attempted), and only somebody actually running the
    harness is stopped by a missing signing key.
    """
    del secret_key  # ordering only: with no API key the suite has already declined

    return provenance_secret_from_env()


def provenance_secret_from_env() -> str:
    """The signing key, or a hard failure. ==Module level so the guardrails can PROVE it fails.==

    A fail-closed nobody exercises is a claim, and this one guards the barrier separating our $1
    from the real customer invoices beside it. Kept out of the fixture body so
    ``test_live_harness_guardrails`` can call it with the variable unset and watch it refuse.
    """
    value = os.environ.get(PROVENANCE_SECRET_ENV, "").strip()
    if not value:
        pytest.fail(
            f"{PROVENANCE_SECRET_ENV} is not set, so phase B could not tell this harness's own "
            "session from any other in the account — and this account holds REAL CUSTOMER "
            "PAYMENTS.\n"
            "\n"
            "==Refusing to run rather than running unprotected.== Export a random secret and keep "
            "it: the SAME value must be present for phase A and phase B of a run.\n"
            "\n"
            "(It is a signing key. It never goes in the repository, and nothing here prints it.)"
        )
    return value


@pytest.fixture
def phase_a_mark(provenance_secret: str) -> Callable[[str], str]:
    """The mark phase A stamps, for a run id. ==Stable across re-runs of the same run.==

    The nonce is minted once per run id and kept in the local state file, NOT regenerated per
    invocation — because phase A's idempotency key is fixed on purpose and Stripe refuses a repeated
    key whose parameters have changed. A fresh nonce would change the return URL, turning the
    deliberate "a re-run replays the same session" behaviour into a 400.
    """

    def _mark(run_id: str) -> str:
        nonce = str(_read_state(run_id).get("nonce") or "")
        if not nonce:
            nonce = secrets.token_urlsafe(32)
            _write_state(run_id, nonce=nonce)
        return _mark_for(run_id, nonce=nonce, secret=provenance_secret)

    return _mark


@pytest.fixture
def record_phase_a_session() -> Callable[[str, str], None]:
    """Persist WHICH session phase A opened. ==The other half of the provenance question.==

    The mark answers *did I create this?*; this answers *is it THE one I created?* Both are needed:
    a mark travels in a URL a guest can read, so it can be copied onto another session — and the
    copy fails, because the id it must also match was written down when phase A opened it.
    """

    def _record(run_id: str, session_id: str) -> None:
        _write_state(run_id, session_id=session_id)

    return _record


@pytest.fixture
def harness_return_url() -> Callable[[str], str]:
    """The return URL this harness stamps on a session, by purpose. ==Its signature on its work.==

    .. rubric:: ==Why provenance is needed at all==

    Phase B refunds whatever Checkout Session id it is handed. Nothing proved that id came from this
    harness — and the Stripe account it runs against carries **real customer invoices**. A mistyped,
    stale or hostile value pointed phase B at somebody else's transaction, and the $1 cap
    distinguishes nothing: it is one amount among many. ==The harness ASSUMED provenance rather than
    ESTABLISHING it==, which is the same failure as every other finding in this change.

    .. rubric:: Why the return URL, and not a metadata update afterwards

    ``metadata`` is the field designed for this, but it would have to be attached by a SECOND call
    after the session exists — and a second call can fail, leaving an unmarked payable session
    behind, which is exactly the window this is supposed to close. The return URL is passed to
    ``create_checkout_session`` itself, so the mark is applied **atomically, at creation, through
    the production code path**. Stripe echoes it back as ``success_url``, and there is no moment
    when a harness session exists without it.

    Distinct segment per purpose, so a checkout-harness session can never be mistaken for a payable
    phase-A one — and the run id is inside the path, so phase B can demand the exact run.
    """

    def _url(purpose: str) -> str:
        return f"{PROVENANCE_BASE}/{purpose}"

    return _url


@pytest.fixture
def require_phase_a_provenance(
    harness_return_url: Callable[[str], str], provenance_secret: str
) -> Callable[[Mapping[str, Any], str], None]:
    """Refuse to act on a session this harness did not open. ==Checked BEFORE any refund.==

    Raises rather than returning a verdict, for the reason ``resolve_money_credential`` raises: a
    boolean here is one hurried ``if`` away from being ignored, and what it guards is somebody
    else's money.

    .. rubric:: ==The finding: a PUBLIC prefix is not proof of authorship==

    The first cut required ``success_url`` to start with a fixed, published path
    (``…/aethercal-live-verification/refund/v1``). Every character of it is in this repository, so
    ==any session in the account could carry it== and pass the one barrier between our $1 and the
    real customer invoices beside it. It proved that somebody had read the source, not that this
    harness had created the session.

    The mark is now an **HMAC** over the purpose, the run id and a per-run nonce, keyed by a secret
    that lives in the environment (:data:`PROVENANCE_SECRET_ENV`) and nowhere in this tree. It is
    applied AT CREATION through the return URL — the one field the production call already carries
    — so no payable session ever exists unmarked. Compared with :func:`hmac.compare_digest`.

    .. rubric:: Two questions, and it needs both answers

    * **did I create this?** — the HMAC. Unforgeable without the key;
    * **is it THE one I created?** — the session id phase A wrote down. ==The mark travels in a URL
      a guest can read==, so a copy of it can be pasted onto another session; that copy fails here,
      because the id will not match. Neither check alone is enough, and the digest is deliberately
      NOT treated as a secret: it is public by construction.

    ==The amount proves nothing== and never did: $1 is one figure among thousands in a real account.

    A refusal names the ``session_id`` — an identifier, not a secret — and never the mark, the nonce
    or the key.
    """

    def _require(session: Mapping[str, Any], run_id: str) -> None:
        state = _read_state(run_id)
        nonce = str(state.get("nonce") or "")
        opened = str(state.get("session_id") or "")
        session_id = session.get("id")

        if not nonce or not opened:
            raise AssertionError(
                f"there is no phase-A record for run id {run_id!r}, so nothing can say which "
                "session this harness opened — and without that, a session id is just a string "
                "somebody typed.\n"
                "\n"
                "==Refusing to touch it.== Run phase A for this run id first. Its state lives "
                f"outside the repository (override the location with {STATE_DIR_ENV}); if phase A "
                "ran on another machine, run phase B there, because the nonce is local to it."
            )

        if session_id != opened:
            raise AssertionError(
                f"session {session_id!r} is not the session phase A opened for run id {run_id!r} "
                f"(that was {opened!r}).\n"
                "\n"
                "==Refusing to touch it.== This account holds real customer payments, and a mark "
                "travels in a URL anybody can read — so matching the mark is not enough on its "
                "own. Phase B refunds the exact session phase A opened, and no other."
            )

        expected_prefix = harness_return_url(f"{PHASE_A_PURPOSE}/{run_id}") + "/"
        success_url = session.get("success_url")
        presented = ""
        if isinstance(success_url, str) and success_url.startswith(expected_prefix):
            presented = success_url[len(expected_prefix) :].split("?", 1)[0]

        expected = _mark_for(run_id, nonce=nonce, secret=provenance_secret)
        if not hmac.compare_digest(presented, expected):
            raise AssertionError(
                f"session {session_id!r} does not carry this harness's AUTHENTICATED mark for run "
                f"id {run_id!r}, so there is nothing to show it was opened by phase A.\n"
                "\n"
                "==Refusing to touch it.== The mark is an HMAC applied at creation, not a public "
                "path anybody could reproduce: a session that merely LOOKS like ours does not "
                "pass. If phase A ran with a different signing key, or on another machine, phase B "
                "cannot verify what it opened.\n"
                "\n"
                "(Neither the expected mark nor the key is shown.)"
            )

    return _require


@pytest.fixture
def open_one_dollar_session(
    secret_key: str, gateway: StripeGateway, expire_session: Callable[[str], str | None]
) -> OpenSession:
    """Open a Checkout Session through the real gateway. ==There is no ``amount_cents`` to pass.==

    The hard cap is enforced by the SHAPE of this seam and not by a rule a test has to follow: a
    test that wanted to create a $100 session would have to stop using this fixture, which is a
    visible edit rather than a mistyped literal.

    ``return_url`` IS a parameter, and deliberately so: it is what marks the session as this
    harness's (:func:`harness_return_url`), and each caller marks its own purpose. It is not a lever
    over money — the two things that are, the amount and the currency, remain unpassable.

    .. rubric:: ==A FAILED creation is not a creation that did not happen==

    Both harnesses guarded everything AFTER this call and nothing around the call itself. If Stripe
    processes the request and the response never lands — a dropped connection, a timeout, a read
    error — this raises, and ==a live, payable $1 session is standing in a real account outside
    every cleanup path this directory has==. The session exists; the object that would name it does
    not, so nothing downstream can expire it. That is the creation-side twin of the defect phase B
    had on the refund side, and it is why the recovery lives HERE, in the one seam both harnesses
    create through, rather than in each of them separately.
    """

    async def _create(
        *, idempotency_key: str, expires_at: datetime, return_url: str
    ) -> CheckoutSession:
        return await gateway.create_checkout_session(
            idempotency_key=idempotency_key,
            amount_cents=ONE_DOLLAR_CENTS,
            currency=CURRENCY,
            expires_at=expires_at,
            return_url=return_url,
            secrets={"secret_key": secret_key},
        )

    async def _recover_an_ambiguous_creation(
        *, idempotency_key: str, expires_at: datetime, return_url: str
    ) -> None:
        """Find out whether a session was created after all, and expire it. ==Never raises.==

        ==The idempotency key IS the persistent reference of the attempt==, so the recovery is a
        REPLAY of the identical request rather than a search: Stripe returns the same session for a
        repeated key within 24 hours. That resolves the ambiguity exactly, in both directions —

        * Stripe DID create one, and the replay hands back that same session, which is expired;
        * Stripe never received it, and the replay creates one now, which is expired immediately.

        Nothing payable is left standing either way, and no money can move in between.

        Replayed through the gateway, with the same arguments, because rebuilding the request by
        hand would be a second definition of the checkout's shape — free to drift from the one under
        test, and a mismatched replay is a 400 for reusing a key with different parameters. The
        EXPIRY goes through the independent client, which is the part that must not share a fault.

        If the replay itself fails there is nothing left to resolve it with, so the run SHOUTS the
        idempotency key: it is the one handle on the attempt, and Stripe offers no lookup by it.
        """
        try:
            replayed = await _create(
                idempotency_key=idempotency_key, expires_at=expires_at, return_url=return_url
            )
        except Exception as exc:
            print(
                "\n"
                "################################################################\n"
                "###  A PAYABLE SESSION MAY BE OPEN. CHECK BY HAND, NOW.      ###\n"
                "################################################################\n"
                f"  idempotency key : {idempotency_key}\n"
                f"  amount          : {ONE_DOLLAR_CENTS} cents {CURRENCY.upper()}\n"
                f"  why             : {_problem('replaying the creation', exc)}\n"
                f"  dashboard       : {DASHBOARD_SESSIONS_URL}\n"
                "  The creation failed AMBIGUOUSLY and the replay that would have resolved it\n"
                "  failed too, so it is unknown whether a live, payable session exists. Stripe\n"
                "  offers no lookup by idempotency key: find the most recent session for this\n"
                "  amount in the dashboard and expire it if it is open.\n"
                "################################################################\n"
            )
            return
        problem = expire_session(replayed.checkout_session_id)
        print(
            "\n!!! the creation failed ambiguously; the replay resolved it to "
            f"{replayed.checkout_session_id}.\n"
            + (
                f"    THE EXPIRY ALSO FAILED: {problem}\n"
                "    A live, payable session may still be OPEN — expire it by hand NOW.\n"
                if problem is not None
                else "    It is expired; nobody can pay it.\n"
            )
        )

    async def _open(
        *, idempotency_key: str, expires_at: datetime, return_url: str
    ) -> CheckoutSession:
        _assert_the_hard_cap()
        try:
            return await _create(
                idempotency_key=idempotency_key, expires_at=expires_at, return_url=return_url
            )
        except BaseException:
            # ==The creation is ambiguous the moment it fails==, so from here the default is
            # RESOLVE IT. The original exception is what the run reports; the recovery never
            # replaces it, exactly as the refund cleanup never replaces the failure explaining it.
            await _recover_an_ambiguous_creation(
                idempotency_key=idempotency_key, expires_at=expires_at, return_url=return_url
            )
            raise

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
