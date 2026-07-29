"""What phase B DOES when it runs. ==Measured by executing it, not by reading it.==

.. rubric:: ==Three iterations of proxies, and this is the fact itself==

The claims these tests make — *provenance is demanded before the refund is sent*, *nothing that can
abort runs outside the guarantee*, *the evidence block is composed only after the fact it certifies*
— are claims about EXECUTION ORDER. They have been approximated three times:

1. **substring** (``"call(" in source``) — a mention in a comment satisfied it;
2. **AST position** — a real ``ast.Call``, but *presence is not execution*: a call inside a nested
   function, a lambda, or a branch that is never taken counts exactly the same as one that runs;
3. **AST position with exclusions** — a better approximation, and the next syntactic construction
   nobody thought to exclude makes it wrong again.

==If the fact is "in what order did it execute", the honest measurement is to execute it.== So phase
B is driven here with doubles that RECORD, and the assertions are about the sequence that actually
happened. There is no approximation left to caducate.

.. rubric:: What stays structural, and why that is not the same compromise

``test_live_harness_guardrails`` keeps its AST guards for claims about SHAPE — "the guarantee is the
last statement", "the creation is wrapped by its recovery", "every provider-touching test declares
the control fixture". Those are statements about how the code is WRITTEN, and reading the code is
the right way to answer them. What moved here is every claim about what HAPPENS.

.. rubric:: Offline, and no credential

Every fixture phase B takes is replaced by a double, so nothing reaches Stripe and nothing needs a
key. That is the point: the money harness's own invariants are now checked on every commit instead
of only on the day somebody runs it for real. This module deliberately carries NO ``live_provider``
marker — it reaches no provider.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Coroutine, Mapping
from datetime import timedelta
from typing import Any

import httpx
import pytest
import test_stripe_live_checkout as checkout_module
import test_stripe_live_refund as phase_b_module

from aethercal.server.services.payments import CheckoutSession, RefundOutcome
from conftest import PHASE_A_PURPOSE, PROVENANCE_BASE

EVIDENCE = "evidence-block-printed"
PAID_SESSION = "cs_live_THE_ONE_PHASE_A_OPENED"
INTENT = "pi_THE_CHARGE"


class _Recorder:
    """The tape. ==Every double writes to it, so the ORDER is the observation.=="""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def note(self, what: str) -> None:
        self.calls.append(what)

    def index(self, what: str) -> int:
        assert what in self.calls, f"`{what}` never ran. The tape was: {self.calls}"
        return self.calls.index(what)


def _session(**overrides: object) -> dict[str, Any]:
    """A paid phase-A session, as Stripe would echo it back."""
    marked = f"{PROVENANCE_BASE}/{PHASE_A_PURPOSE}/v1/a-mark?checkout=success"
    return {
        "id": PAID_SESSION,
        "amount_total": 100,
        "currency": "usd",
        "payment_status": "paid",
        "status": "complete",
        "payment_intent": INTENT,
        "livemode": True,
        "success_url": marked,
    } | overrides


class _Response:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Mapping[str, Any]:
        return self._payload


class _StripeApiDouble:
    """Routes by URL, like the real client does."""

    def __init__(self, session: Mapping[str, Any]) -> None:
        self._session = session

    def get(self, url: str, **_: object) -> _Response:
        if url.startswith("/checkout/sessions"):
            return _Response(self._session)
        if url.startswith("/refunds"):
            return _Response({"data": [{"id": "re_1", "status": "succeeded", "amount": 100}]})
        if url.startswith("/payment_intents"):
            return _Response({"latest_charge": "ch_1"})
        raise AssertionError(f"phase B asked for an unexpected URL: {url}")


class _GatewayDouble:
    """The gateway, which may answer — or blow up. ==Both are states it really reaches.=="""

    def __init__(self, tape: _Recorder, *, raises: Exception | None = None) -> None:
        self._tape = tape
        self._raises = raises

    async def refund(self, **_: object) -> RefundOutcome:
        self._tape.note("refund")
        if self._raises is not None:
            raise self._raises
        return RefundOutcome.succeeded("re_1")


def _drive(  # noqa: PLR0913 - each argument arranges one state phase B really reaches
    tape: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: Mapping[str, Any] | None = None,
    provenance_fails: bool = False,
    settled: str | None = None,
    gateway_raises: Exception | None = None,
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Build the coroutine that runs phase B against doubles. ==The real function, real flow.=="""

    def _provenance(_session: Mapping[str, Any], _run_id: str) -> None:
        tape.note("provenance")
        if provenance_fails:
            raise AssertionError("does not carry this harness's mark")

    def _ensure(_intent: str, _key: str) -> str | None:
        tape.note("ensure_refunded")
        return settled

    def _shout(_intent: str, _problem: str) -> None:
        tape.note("shout")

    real_print = builtins.print

    def _print(*args: object, **kwargs: object) -> None:
        if args and isinstance(args[0], str) and "=== EVIDENCE for" in args[0]:
            tape.note(EVIDENCE)
        real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", _print)

    async def _run() -> None:
        await phase_b_module.test_phase_b_refunds_the_real_charge_through_the_gateway(
            stripe_reachable=401,
            paid_session_id=PAID_SESSION,
            refund_reconnected=None,
            secret_key="sk_test_NOT_A_REAL_KEY_FOR_A_DOUBLE",
            gateway=_GatewayDouble(tape, raises=gateway_raises),
            one_dollar_cents=100,
            one_dollar_currency="usd",
            terminal_refund_success="succeeded",
            stripe_api=_StripeApiDouble(session or _session()),
            require_phase_a_provenance=_provenance,
            ensure_refunded=_ensure,
            shout_that_money_is_held=_shout,
        )

    return _run


async def test_provenance_really_runs_before_the_refund(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The order, observed.== Not "the call appears earlier in the file" — it RAN earlier.

    A call sitting in a nested function or an untaken branch reads identically to one that
    executes, which is exactly what an AST position check cannot tell apart.
    """
    tape = _Recorder()

    await _drive(tape, monkeypatch)()

    assert tape.index("provenance") < tape.index("refund"), tape.calls


async def test_a_refused_session_is_never_refunded(monkeypatch: pytest.MonkeyPatch) -> None:
    """==The barrier's whole purpose, and only execution can show it.==

    Provenance raising must stop the run BEFORE the gateway is touched. An ordering check cannot
    say this: it proves the call sites sit in some sequence, not that failing the first prevents
    the second. This account holds real customer payments — a stranger's charge must never be
    refunded, and "never" is a statement about what happens.
    """
    tape = _Recorder()

    with pytest.raises(AssertionError, match="does not carry this harness's mark"):
        await _drive(tape, monkeypatch, provenance_fails=True)()

    assert "refund" not in tape.calls, (
        f"the gateway was called on a session the harness had already refused. Tape: {tape.calls}"
    )
    assert "ensure_refunded" not in tape.calls, (
        "the refund guarantee opened around a session that was refused — a stranger's payment must "
        f"never become a target at all. Tape: {tape.calls}"
    )


async def test_a_failed_validation_still_returns_the_money(monkeypatch: pytest.MonkeyPatch) -> None:
    """==H3's invariant, measured instead of inferred.==

    A session that is genuinely PAID but fails a validation — here, an amount that does not match
    the hard cap — must still reach ``ensure_refunded``. The structural guard says "no assert sits
    outside the try"; this says the dollar actually comes back.
    """
    tape = _Recorder()

    with pytest.raises(AssertionError):
        await _drive(tape, monkeypatch, session=_session(amount_total=4200))()

    assert "ensure_refunded" in tape.calls, (
        "a validation failed on a PAID session and the money was never sent back. Tape: "
        f"{tape.calls}"
    )


async def test_the_evidence_is_printed_only_after_the_money_is_confirmed_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==What certifies must come after what measures.== The block is pasted into the register.

    ``ensure_refunded`` is the only thing that knows the money is actually back; the evidence block
    is what a human copies into ``live_verifications()``. Certifying first would put a claim about
    the future into the money guard.
    """
    tape = _Recorder()

    await _drive(tape, monkeypatch)()

    # ==The anti-vacuity half of `…shouted_about_and_never_certified`.== That test asserts the
    # block is ABSENT when the money is stuck; without this one, it would pass just as well over a
    # harness that had stopped certifying anything at all.
    assert EVIDENCE in tape.calls, tape.calls
    assert tape.index("ensure_refunded") < tape.index(EVIDENCE), tape.calls


async def test_money_left_behind_is_shouted_about_and_never_certified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==Three facts, and proving one of them proved nothing about the other two.==

    The first cut asserted only that the alarm SOUNDS. A run that shouted *and also printed the
    evidence block* would have passed it — and that block is what a human pastes into
    ``live_verifications()``, so it would have certified "the money went back" in the same breath
    as "the money is still held". ==Certifying while alarming is worse than not alarming==: the
    register ends up holding a claim the alarm itself contradicts.

    So all three are asserted:

    * the alarm is REACHED — an alarm nothing calls is a string in a file;
    * the certificate is NOT written — the run that could not return the money certifies nothing;
    * the attempt comes BEFORE the alarm — shouting without having tried to return the money is a
      report about something nobody did.
    """
    tape = _Recorder()

    with pytest.raises(AssertionError, match="STILL NOT refunded"):
        await _drive(tape, monkeypatch, settled="42 of 100 cents are STILL NOT refunded")()

    assert "shout" in tape.calls, tape.calls
    assert EVIDENCE not in tape.calls, (
        "the run printed the EVIDENCE block while reporting that money was still held. That block "
        f"is pasted into live_verifications(); this one would certify a refund that did not "
        f"happen. Tape: {tape.calls}"
    )
    assert tape.index("ensure_refunded") < tape.index("shout"), (
        "the alarm fired before anything tried to send the money back, so it reports on an attempt "
        f"that was never made. Tape: {tape.calls}"
    )


# ======================================================================================
# The checkout harness makes the same ORDER claim, so it gets the same measurement.
# ======================================================================================


class _CheckoutGatewayDouble:
    @property
    def checkout_session_floor(self) -> timedelta:
        return timedelta(minutes=30)


class _CheckoutApiDouble:
    def get(self, url: str, **_: object) -> _Response:
        del url
        return _Response(
            {
                "id": "cs_live_X",
                "status": "open",
                "payment_status": "unpaid",
                "amount_total": 100,
                "payment_intent": None,
                "livemode": True,
            }
        )


async def test_the_checkout_harness_expires_before_it_certifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The same claim, in the other harness, measured the same way.==

    The checkout evidence block once said "expired afterwards" while the expiry still sat in the
    `finally` BELOW the print — a claim about the future, written as though it had been observed.
    That block is what somebody pastes into ``live_verifications()``.

    ==Swept because it is the same CLASS, not because a gate asked.== Fixing a defect only where it
    was reported is how this branch ended up with one AST classifier and four substring guards.
    """
    tape = _Recorder()

    async def _open(**_: object) -> CheckoutSession:
        tape.note("created")
        return CheckoutSession(
            checkout_url="https://checkout.stripe.com/x", checkout_session_id="cs_live_X"
        )

    def _expire(_session_id: str) -> str | None:
        tape.note("expired")
        return None

    real_print = builtins.print

    def _print(*args: object, **kwargs: object) -> None:
        if args and isinstance(args[0], str) and "=== EVIDENCE for" in args[0]:
            tape.note(EVIDENCE)
        real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", _print)

    await checkout_module.test_the_gateway_opens_a_real_checkout_session_and_expires_it(
        stripe_reachable=401,
        gateway=_CheckoutGatewayDouble(),
        open_one_dollar_session=_open,
        one_dollar_cents=100,
        stripe_api=_CheckoutApiDouble(),
        expire_session=_expire,
        harness_return_url=lambda purpose: f"{PROVENANCE_BASE}/{purpose}",
    )

    assert tape.index("expired") < tape.index(EVIDENCE), tape.calls


async def test_a_gateway_that_blows_up_still_returns_the_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The case the guarantee was BUILT for, and the one nothing exercised.==

    A timeout, a 500, the network dropping mid-call: the gateway's refund raising is the whole
    reason phase B wraps it in a ``try``/``finally``. The other execution tests fail the things
    AROUND the call — a validation, the settlement — and then assume the guarantee holds when the
    call ITSELF fails. ==That is the same mistake as asserting the alarm sounds without asserting
    the certificate is absent: proving the neighbouring facts and taking the central one on
    trust.==

    Three things must be true, and none of them follows from the others:

    * the exception PROPAGATES — a harness that swallowed a gateway failure would report a green
      run over a refund that never left;
    * ``ensure_refunded`` ran anyway — the ``finally`` did its job, which is the guarantee itself;
    * it ran AFTER the attempt, so it is a response to the failure and not a coincidence.

    And nothing is certified: a call that exploded is the last place a run may claim the money
    went back.
    """
    tape = _Recorder()
    dropped = httpx.ConnectError("the connection dropped mid-refund")

    with pytest.raises(httpx.ConnectError) as raised:
        await _drive(tape, monkeypatch, gateway_raises=dropped)()

    assert raised.value is dropped, (
        "the propagating exception is not the one the gateway raised — something between it and "
        "the operator replaced it"
    )
    assert "ensure_refunded" in tape.calls, (
        "the gateway blew up and NOTHING tried to send the money back — the `finally` that exists "
        f"for exactly this did not run. Tape: {tape.calls}"
    )
    assert tape.index("refund") < tape.index("ensure_refunded"), (
        f"the guarantee ran before the attempt it is meant to cover. Tape: {tape.calls}"
    )
    assert EVIDENCE not in tape.calls, (
        "the run certified a refund whose provider call raised. That block is pasted into "
        f"live_verifications(). Tape: {tape.calls}"
    )


async def test_a_cleanup_failure_never_replaces_the_gateway_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """==The worst pair: the call explodes AND the money cannot be recovered.==

    Here both go wrong at once. The run must shout about the stuck money, and ==the exception that
    reaches the operator must still be the ORIGINAL one== — the cleanup's own complaint must never
    replace the failure that explains it, or the report says "money is stuck" while hiding that the
    gateway is what broke.

    The code says this in a comment (*"a cleanup error must never replace the failure that explains
    it"*); nothing executed it until now.
    """
    tape = _Recorder()
    dropped = httpx.ConnectError("the connection dropped mid-refund")

    with pytest.raises(httpx.ConnectError) as raised:
        await _drive(
            tape,
            monkeypatch,
            gateway_raises=dropped,
            settled="100 of 100 cents are STILL NOT refunded",
        )()

    # ==IDENTITY, not class.== Both stories end in a `ConnectError`: the gateway's, and a cleanup
    # that raised its own on the way out. The class is compatible with the thing being proved AND
    # with the thing being feared, so it distinguishes nothing — which is the defect this whole
    # test exists to catch, appearing inside the test itself.
    assert raised.value is dropped, (
        "the exception that reached the operator is not the one the gateway raised. A cleanup "
        "failure replaced the failure that explains it, so the report names the stuck money and "
        "hides that the provider call is what broke."
    )
    assert "shout" in tape.calls, f"money was left stuck and nobody was told. Tape: {tape.calls}"
    assert EVIDENCE not in tape.calls, tape.calls
