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

import ast
import inspect
import pathlib
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest
from live_harness_modules import provider_touching_modules

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
    assert set(parameters) == {"idempotency_key", "expires_at", "return_url"}, (
        f"the session opener takes {sorted(parameters)}. The idempotency key, the expiry and the "
        "return URL (which carries the harness's provenance mark) are the only levers it may "
        "offer; anything else is a lever over real money that a test should not have."
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


EVIDENCE_MARKER = "=== EVIDENCE for live_verifications"

MUST_BE_ESTABLISHED_BEFORE_CERTIFYING = {
    # The evidence block claimed "expired afterwards" while the expiry still sat in the `finally`
    # BELOW the print — an observation about the future, written as though it had been made.
    "test_stripe_live_checkout.py": "expire_session(session.checkout_session_id)",
    # The evidence block accepted a `pending` refund and then printed "The money went back". The
    # cleanup had been taught that pending is not done; the thing printing the certificate had not.
    "test_stripe_live_refund.py": "ensure_refunded(payment_intent_id, idempotency_key)",
}
"""For each harness: the call that ESTABLISHES the fact, which must run before the block that
CERTIFIES it. See :func:`test_no_harness_certifies_a_fact_before_establishing_it`."""


def test_no_harness_certifies_a_fact_before_establishing_it() -> None:
    """==The pattern that bit twice in one review: what certifies is not what measures.==

    Both defects were the same shape one layer apart. The cleanup was taught that a `pending`
    refund is not money returned — and the evidence block, which is what gets pasted into
    `live_verifications()`, went on saying "the money went back" anyway. The checkout harness
    claimed "expired afterwards" before anything had expired. ==A false evidence block is worse
    than a failed test==: it is the input to the register the money guard reads, so a lie there
    becomes a lie in the guard.

    So the ordering is pinned: the call that establishes the fact must appear before the block that
    certifies it.

    .. note::

       ==This proves ORDER, not correctness.== It cannot tell whether the right thing was verified —
       the runtime assertions next to each call do that (`assert unsettled is None`,
       `assert problem is None`, `status == terminal_refund_success`). What it catches is the
       specific regression that has now happened twice: somebody moves or adds a print, and the
       certificate goes back to being written before the measurement.
    """
    for module, establishing_call in MUST_BE_ESTABLISHED_BEFORE_CERTIFYING.items():
        source = (pathlib.Path(__file__).parent / module).read_text(encoding="utf-8")

        assert EVIDENCE_MARKER in source, (
            f"{module} no longer prints an evidence block, so this guard is watching nothing. If "
            "the harness moved, move this with it."
        )
        assert establishing_call in source, (
            f"{module} no longer calls `{establishing_call}`, so nothing establishes the fact its "
            "evidence block certifies."
        )
        assert source.index(establishing_call) < source.index(EVIDENCE_MARKER), (
            f"{module} composes its EVIDENCE block before `{establishing_call}` has run. That "
            "block is pasted into live_verifications() — certifying a fact that has not been "
            "established yet is how a false record reaches the money guard."
        )


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


def _foreign_paid_session() -> dict[str, Any]:
    """A real customer's payment, as Stripe would return it. ==Same amount, same currency, paid.==

    This is the session the harness must refuse: everything about it looks exactly like phase A's
    except the one thing that matters — it was not opened by this harness. An invoice paid through
    the agency's own Stripe account is precisely this shape.
    """
    return {
        "id": "cs_live_SOMEBODY_ELSES_PAYMENT",
        "amount_total": 100,
        "currency": "usd",
        "payment_status": "paid",
        "status": "complete",
        "payment_intent": "pi_SOMEBODY_ELSES_PAYMENT",
        "success_url": "https://crm.example.com/invoice/paid?id=INV-2026-00042",
    }


def test_a_paid_session_of_the_same_amount_is_refused_without_provenance(
    require_phase_a_provenance: Callable[[Mapping[str, Any], str], None],
) -> None:
    """==The finding: phase B would refund any session id it was handed.==

    Nothing proved the id came from phase A, and the account this runs against carries **real
    customer invoices**. A mistyped, stale or hostile value pointed the refund at somebody else's
    transaction — and ==the $1 cap defends nothing==, because $1 is one figure among thousands. The
    harness ASSUMED provenance instead of ESTABLISHING it.

    The session below is paid, in USD, for exactly the harness's amount. It is refused anyway,
    because it does not carry the mark phase A applies at creation.
    """
    with pytest.raises(AssertionError, match="does not carry this harness's mark"):
        require_phase_a_provenance(_foreign_paid_session(), "v1")


def test_a_session_from_a_different_run_is_refused(
    require_phase_a_provenance: Callable[[Mapping[str, Any], str], None],
    harness_return_url: Callable[[str], str],
) -> None:
    """The mark carries the RUN ID, so phase B refunds the run it was asked about and no other.

    Two payable sessions can exist at once (a lapsed ``v1``, a fresh ``v2``). Refunding whichever
    one happened to be pasted would be the same class of mistake in miniature.
    """
    session = _foreign_paid_session() | {"success_url": f"{harness_return_url('refund/v2')}?x=1"}

    with pytest.raises(AssertionError, match="does not carry this harness's mark"):
        require_phase_a_provenance(session, "v1")


def test_a_prefix_of_the_run_id_is_not_a_match(
    require_phase_a_provenance: Callable[[Mapping[str, Any], str], None],
    harness_return_url: Callable[[str], str],
) -> None:
    """==Run id ``v1`` must not match a session marked ``v11``.==

    The check requires the query-string boundary right after the run id, so one id cannot pass as
    the prefix of another. Without that boundary this would be a silent, plausible-looking match.
    """
    session = _foreign_paid_session() | {"success_url": f"{harness_return_url('refund/v11')}?x=1"}

    with pytest.raises(AssertionError, match="does not carry this harness's mark"):
        require_phase_a_provenance(session, "v1")


def test_a_session_this_harness_opened_is_accepted(
    require_phase_a_provenance: Callable[[Mapping[str, Any], str], None],
    harness_return_url: Callable[[str], str],
) -> None:
    """==Anti-vacuity.== A check that refused everything would pass all three tests above and make
    phase B impossible to run at all.

    The URL is built exactly as the gateway builds it: the harness's return URL, then the query
    string ``create_checkout_session`` appends.
    """
    query = "?checkout=success&session_id={CHECKOUT_SESSION_ID}"
    session = _foreign_paid_session() | {"success_url": harness_return_url("refund/v1") + query}

    require_phase_a_provenance(session, "v1")  # must not raise


def test_provenance_is_demanded_before_the_refund_is_sent() -> None:
    """==A correct check that runs too late is not a check.==

    The decision above is only worth anything if phase B consults it BEFORE it acts. This pins the
    order in the source, the same way the evidence guard does — and for the same reason: the two
    defects this harness keeps producing are *a fact asserted before it was established* and *a
    guard consulted after the thing it guards*.
    """
    source = (pathlib.Path(__file__).parent / "test_stripe_live_refund.py").read_text(
        encoding="utf-8"
    )

    demand = "require_phase_a_provenance(session, _run_id())"
    refund_call = "await gateway.refund("
    assert demand in source, "phase B no longer demands provenance at all"
    assert refund_call in source, "phase B no longer calls the gateway's refund"
    assert source.index(demand) < source.index(refund_call), (
        "phase B sends the refund before it has established that the session came from phase A"
    )


CONTROL_FIXTURE = "stripe_reachable"


def test_every_provider_touching_test_asks_for_the_connectivity_control() -> None:
    """==The finding: the control could be selected around.==

    The control that proves this process really reaches Stripe used to be a TEST standing beside the
    runs it vouched for. ``pytest <file>::<test>`` runs one of them alone, and pytest promises no
    ordering even when both are collected. So the run that ==prints the record somebody pastes into
    ``live_verifications()``== could happen with the control never executed — evidence about a
    round-trip nobody watched, feeding the guard that stands between this product and a real charge
    on a real card.

    It is a fixture now, and a fixture cannot be selected around — but only for the tests that ASK.
    This is what makes asking non-optional: every test in every provider-touching module must name
    :data:`CONTROL_FIXTURE` in its signature. A new harness that forgets fails HERE, offline, on
    every commit — not on the day somebody writes its evidence into the register.

    ==It reads the whole directory, not a list==, so "which tests must ask?" cannot drift from
    "which tests reach a provider". THIS file is deliberately not among them: it carries no marker,
    reaches nothing, and its own mention of the marker is a string rather than an assignment — a
    distinction ``live_harness_modules`` makes structurally, because the substring version
    classified this very guard as a provider harness the moment it named what it was looking for.
    """
    modules = provider_touching_modules(pathlib.Path(__file__).parent)
    assert modules, (
        "no provider-touching module was found in tests/live/, so this guard is watching nothing. "
        "Either the harness moved or the marker was renamed."
    )

    missing: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            parameters = {argument.arg for argument in node.args.args}
            if CONTROL_FIXTURE not in parameters:
                missing.append(f"{path.name}::{node.name}")

    assert not missing, (
        f"these tests reach a real provider without asking for `{CONTROL_FIXTURE}`: {missing}. Run "
        "one of them by name and it produces evidence — or opens a payable session — with nothing "
        "having shown this process reaches Stripe at all. Add the fixture to the signature; it "
        "costs one zero-cost GET and it is what makes the evidence mean anything."
    )


PHASE_B = "test_phase_b_refunds_the_real_charge_through_the_gateway"
REFUND_GUARANTEE = "ensure_refunded"


def _phase_b() -> ast.AsyncFunctionDef:
    """Phase B's own syntax tree. ==Parsed, not grepped== — this is a claim about STRUCTURE."""
    source = (pathlib.Path(__file__).parent / "test_stripe_live_refund.py").read_text(
        encoding="utf-8"
    )
    for node in ast.parse(source).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == PHASE_B:
            return node
    raise AssertionError(f"{PHASE_B} is gone, so this guard is watching nothing")


def _guarantees_the_refund(node: ast.stmt) -> bool:
    """A ``try`` whose ``finally`` calls :func:`ensure_refunded` — the money's safety net."""
    if not isinstance(node, ast.Try):
        return False
    return any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == REFUND_GUARANTEE
        for statement in node.finalbody
        for inner in ast.walk(statement)
    )


def test_every_validation_after_the_payment_intent_is_inside_the_refund_guarantee() -> None:
    """==The finding: the money's safety net started AFTER four assertions that could abort.==

    Phase B validated the currency, the ``paid`` status, the amount and the PaymentIntent's shape
    and only THEN opened the ``try``/``finally`` that guarantees the refund. Every one of those can
    fail on a session a human has genuinely paid — and when one did, the run ended with ==a real
    dollar on a real card and nothing left to send it back==. The validations were guarding the run;
    nothing was guarding the money.

    So the structure is pinned: once there is a PaymentIntent to aim a refund at, ==nothing that can
    abort may run outside the guarantee==.

    .. note::

       This proves STRUCTURE, not correctness — like
       :func:`test_no_harness_certifies_a_fact_before_establishing_it` above. It cannot tell whether
       the right things are validated; what it catches is the regression that already happened once:
       somebody adds "just one quick check" above the ``try``, and a failure of that check strands
       somebody's dollar.
    """
    phase_b = _phase_b()
    guarantees = [node for node in phase_b.body if _guarantees_the_refund(node)]

    assert len(guarantees) == 1, (
        f"{PHASE_B} has {len(guarantees)} top-level `try` blocks whose `finally` calls "
        f"`{REFUND_GUARANTEE}`; there must be exactly one, or 'is the money guaranteed?' has no "
        "single answer"
    )
    guarantee = guarantees[0]

    assert phase_b.body[-1] is guarantee, (
        "the refund guarantee is not the last thing phase B does, so something runs after the "
        "`finally` that returns the money — outside the only thing protecting it"
    )

    unprotected = [
        node.lineno
        for statement in phase_b.body[: phase_b.body.index(guarantee)]
        for node in ast.walk(statement)
        if isinstance(node, ast.Assert)
    ]
    assert not unprotected, (
        f"{PHASE_B} asserts at line(s) {unprotected}, BEFORE the `try` whose `finally` guarantees "
        "the refund. A real card may already have been charged by then: if one of those assertions "
        "fails, the run stops and the money stays on it. Move the check inside the guarantee."
    )


def test_the_payment_intent_is_resolved_before_the_guarantee_but_after_provenance() -> None:
    """==The two barriers are in tension, and the order between them is the whole design.==

    The refund guarantee cannot open before there is a PaymentIntent to aim at — so the id is
    resolved early. But resolving it early must NOT come before provenance: pointing the refund
    machinery at a stranger's payment would be worse than stranding our own dollar, and this account
    carries real customer invoices.

    So: provenance, then the id, then the guarantee. All three, in that order, checked here rather
    than remembered.
    """
    source = (pathlib.Path(__file__).parent / "test_stripe_live_refund.py").read_text(
        encoding="utf-8"
    )
    demand = "require_phase_a_provenance(session, _run_id())"
    resolve = 'payment_intent_id = session.get("payment_intent")'
    keyed = "idempotency_key = refund_dedupe_key(payment_intent_id)"

    for needle in (demand, resolve, keyed):
        assert needle in source, f"phase B no longer contains `{needle}`"

    assert source.index(demand) < source.index(resolve), (
        "phase B resolves the PaymentIntent before it has established that the session came from "
        "phase A — so a foreign payment becomes a target before anything refuses it"
    )
    assert source.index(resolve) < source.index(keyed), (
        "phase B derives the refund key before resolving the PaymentIntent it is derived from"
    )


def test_an_unresolvable_payment_intent_on_a_paid_session_raises_the_alarm() -> None:
    """==The one path that cannot be guaranteed must SHOUT, and must name the session.==

    If the PaymentIntent cannot be read off a session that says ``paid``, there is real money and no
    way to aim a refund at it — ``ensure_refunded`` takes the very id that is missing. That is the
    worst state this harness can reach, so the branch that handles it is checked here: it must be
    reachable, it must name the session id, and it must be unmistakable.

    Read from the source rather than executed, because executing it needs a live credential and a
    paid session — the two things this offline file deliberately does not have.
    """
    source = (pathlib.Path(__file__).parent / "test_stripe_live_refund.py").read_text(
        encoding="utf-8"
    )

    assert "MONEY MAY BE HELD" in source, (
        "phase B no longer shouts when a PAID session's PaymentIntent cannot be resolved. That is "
        "real money nothing can return automatically, and silence is how nobody goes looking."
    )
    assert "{paid_session_id}" in source, (
        "the alarm must name the session id — with no PaymentIntent it is the only handle a human "
        "has left"
    )
    assert "DASHBOARD_SEARCH_URL" in source, (
        "the alarm must say WHERE to refund by hand; an alarm without the next action is a shrug"
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


CREATION_CALL = "create_checkout_session"
CREATION_RECOVERY = "_recover_an_ambiguous_creation"
CREATION_PRIMITIVE = "_create"


def _called_names_in(node: ast.AST) -> set[str]:
    """Every name called anywhere inside ``node`` — ``a.b()`` and ``b()`` both give "b"."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        called = inner.func
        if isinstance(called, ast.Attribute):
            names.add(called.attr)
        elif isinstance(called, ast.Name):
            names.add(called.id)
    return names


def test_the_creation_of_a_payable_session_is_inside_its_own_recovery() -> None:
    """==The H3 defect on the CREATION side: a failed create is not a create that did not happen.==

    Both harnesses guarded everything after the session existed and nothing around the call that
    brings it into existence. If Stripe processes the request and the response never lands — a
    dropped connection, a timeout, a read error — the call raises with a live, payable $1 session
    standing in a real account and no object naming it, so every cleanup path in this directory is
    blind to it.

    The fix lives in the ONE seam both harnesses create through
    (``conftest.open_one_dollar_session``), and this pins it there: the function that calls the
    gateway's creation must also call the recovery. Written structurally, because "remember to wrap
    the create" is exactly the kind of rule that holds until somebody adds a third harness.

    .. note::

       ==Structure, not correctness.== That the replay actually resolves the ambiguity is a property
       of Stripe's idempotency, argued in the recovery's own docstring; what this catches is the
       omission — a creation with nothing at all around it.
    """
    conftest = pathlib.Path(__file__).parent / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"))
    functions = {
        node: _called_names_in(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    # Transitively: `_open` reaches the creation through `_create`, so asking only about DIRECT
    # callers would watch one line and miss every wrapper anybody adds around it.
    creators = {node for node, calls in functions.items() if CREATION_CALL in calls}
    while True:
        named = {node.name for node in creators}
        grown = {node for node, calls in functions.items() if calls & named}
        if grown <= creators:
            break
        creators |= grown

    assert creators, (
        f"nothing in the live conftest reaches `{CREATION_CALL}` any more, so this guard is "
        "watching nothing. If the creation seam moved, move this with it."
    )

    # Two functions are the mechanism rather than users of it: the primitive that IS the call (it
    # has no error handling by design — its callers own the recovery), and the recovery itself,
    # which replays through the primitive and would otherwise be required to recover itself.
    unrecovered = sorted(
        node.name
        for node in creators
        if node.name not in {CREATION_PRIMITIVE, CREATION_RECOVERY}
        and CREATION_RECOVERY not in functions[node]
    )
    assert not unrecovered, (
        f"these reach a payable session's creation without `{CREATION_RECOVERY}` around it: "
        f"{unrecovered}. A create that fails AFTER Stripe processed it leaves a live $1 invitation "
        "nothing can expire, because the id that names it never came back."
    )
