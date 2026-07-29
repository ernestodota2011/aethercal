"""BYOK — a business's own provider credentials: precedence, and the one place money is different.

Every provider this product talks to used to be configured **once, for the whole instance, from the
environment**: one SMTP relay, one WhatsApp number, one SMS account (``app.build_email_sender`` /
``app.build_channel_senders``). This module is where a business brings its own instead.

.. rubric:: Precedence — the BUSINESS's credential wins; the environment is the instance's DEFAULT

A row in ``tenant_credentials`` beats the environment. The environment stops being *the* credential
and becomes the *default* one.

.. rubric:: ==And for MONEY there is no default. That asymmetry is the point of this module.==

A business with no SMTP credential of its own still sends its mail through the instance's relay. A
business with no PAYMENT credential of its own ==does not charge at all==. It does not fall back.

The two are not the same kind of act, and the difference is not one of degree:

* sending a mail with the instance's relay is **infrastructure the operator lends**. A single-
  business self-hoster configures ``AETHERCAL_SMTP_*`` once and everything works, which is exactly
  what a self-hostable product ought to do;
* taking a guest's money into the instance operator's payment account is **charging with somebody
  else's account** — a different act, a different failure, and a different word for it. It does not
  become acceptable because the code path was convenient.

==Charging with another party's account is a qualitatively different failure from sending an email
with the instance's SMTP relay.== So the fallback does not exist on the money path — not as a flag,
not as an optional argument, and not as a ``None`` some caller might read generously:

* :func:`resolve_money_credential` **has no** ``instance_default`` parameter. There is nothing to
  pass. With no row for that business it RAISES (:class:`MissingCredentialError`);
* :func:`resolve_infra_credential` — the one door that *can* fall back — **refuses a provider that
  handles money** (:class:`WrongCredentialClassError`), so the fallback cannot be reached by routing
  a payment provider through it;
* :func:`credential_class` is an ``assert_never`` match, so a NEW provider does not type-check until
  somebody has said which side of that line it falls on. The decision cannot be skipped by default.

.. rubric:: ==CUSTODY — what the encryption actually protects, stated without varnish==

The Fernet key is derived from the instance's single ``AETHERCAL_APP_SECRET``
(:func:`~aethercal.server.crypto.derive_fernet_key`). ==**ONE key encrypts the credentials of EVERY
business on the instance.**==

That is **encryption at rest. It is NOT cryptographic isolation.** ==Whoever operates the instance
can decrypt any business's credential== — they hold the app secret, and the key is a pure function
of it. Read that sentence as written: an instance operator who wants to read a business's payment
keys can do so, and nothing in this design prevents it. What the encryption buys is real, and it is
narrower than it looks: a stolen database dump, a leaked backup, a misconfigured replica or a
SQL-injection read is **useless without the app secret**, which lives in the process environment and
not in the database.

Two further facts the reader is entitled to, because they follow from the design and would otherwise
be discovered by surprise:

* ==**the web process and the worker both decrypt BYOK credentials in flight.**== The web creates
  the checkout session and verifies the inbound webhook's signature; the worker executes the effects
  (the refund, the message). Both do it under row-level security with the business bound — so a
  process only ever decrypts the credential of the business it is currently acting for — but both
  hold the instance key, because both must;
* the key is derived deterministically from the app secret, so rotating the key means rotating the
  app secret and re-encrypting every stored credential
  (:mod:`aethercal.server.services.key_rotation`).

This is **accepted, and it is documented rather than dressed up** (``docs/byok-credentials.md``).
Whoever requires that the operator be *unable* to decrypt their credentials needs an instance of
their own — that is a real answer, and it is the honest one. A per-business key (cryptographic
isolation, so that one business's credentials cannot be decrypted with another's, and the operator
cannot decrypt at all) is out of scope here and is named as such in the specification.

==A product that promises more isolation than it delivers is worse than one that is honest about
what it has.==
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import decrypt_secret, encrypt_secret
from aethercal.server.db.models import TenantCredential


class CredentialProvider(StrEnum):
    """Every provider a business may bring its own credential for. The stored ``provider`` value."""

    STRIPE = "stripe"
    MERCADO_PAGO = "mercado_pago"
    SMTP = "smtp"
    WHATSAPP = "whatsapp"
    SMS = "sms"


class CredentialClass(StrEnum):
    """MONEY or INFRA — and the whole of the fallback rule reads off this one distinction."""

    MONEY = "money"
    """It moves somebody else's money. ==There is no instance default. Ever.=="""

    INFRA = "infra"
    """It sends something. The instance's own configuration is a legitimate default."""


class CredentialSource(StrEnum):
    """Where a resolved credential came from — the business, or the instance's own configuration."""

    TENANT = "tenant"
    INSTANCE = "instance"


class CredentialError(RuntimeError):
    """Base class for every refusal in this module."""


class MissingCredentialError(CredentialError):
    """==A business with no payment credential of its own does not charge.== Criterion 41.

    Raised rather than returned, deliberately: a ``None`` here would be read by the first hurried
    caller as "nothing configured, use the default" — the exact sentence this module exists to make
    unsayable.
    """


class AmbiguousMoneyProviderError(CredentialError):
    """==A business has TWO payment credentials, and nothing says which one to charge with.==

    Raised, never resolved by picking one. There is no per-tenant preference field, so with both
    Stripe and Mercado Pago configured there is **no fact in the system** that answers "which
    account does this business want its guests' money in?" — and the failure mode of guessing is not
    a visible error. It is money landing in the wrong account while every status code says success.

    Deliberately NOT a subclass of :class:`MissingCredentialError`. The two are different facts —
    "you have configured none" is a setup step the business has not done; "you have configured two"
    is a misconfiguration a human must resolve — and a subclass relationship would let a caller
    catch the one and silently absorb the other.

    ==The debt, stated with its real price.== If this refusal proves a nuisance in practice, the fix
    is a **per-tenant payment-provider preference** — a column, a migration and an admin control, so
    the business STATES its choice and the system reads it. That is a product decision with a
    migration attached. It is emphatically NOT a default (``prefer stripe``) added quietly to make
    this exception go away: a default here is indistinguishable from the guess this exists to
    prevent, and it would be discovered by whoever's money went to the wrong place.
    """


class WrongCredentialClassError(CredentialError):
    """A provider was routed through the door meant for the other class.

    Raised both ways round. A money provider through :func:`resolve_infra_credential` would reach
    the instance-default fallback — the bypass of criterion 41 — and an infra provider through
    :func:`resolve_money_credential` would turn an unconfigured mail relay into a hard failure of
    the booking flow, which it has never been.
    """


class IncompleteCredentialError(CredentialError):
    """A credential was stored without every field its provider needs in order to work.

    ==A credential that exists but cannot finish its job is worse than none at all.== A Stripe
    credential with no webhook secret can start a charge and can never verify its confirmation: the
    money leaves the guest's card and the booking is never confirmed — which this specification
    calls the worst outcome the system can produce.
    """


class UnrecognisedCredentialError(CredentialError):
    """A value is not a recognisable key of that provider AT ALL. ==Checked for ever, not for now.==

    ==Distinct from :class:`LiveCredentialRefusedError`, and the distinction is the point.== The
    other says *this is a real key, in the wrong mode for what we have proved* — a statement about
    evidence, which changes as evidence accumulates. This says *this is not a key*, which no amount
    of verification will ever make untrue.

    They were the same check once, and that is precisely how the type validation came to have an
    expiry date: it rode on the mode guard, so it disappeared the moment a provider became fully
    verified — leaving a truncated paste or a key from the wrong account to be stored unexamined, at
    exactly the moment the system began moving real money.

    Reported separately for the same reason :class:`IncompleteCredentialError` is: telling an
    operator that rubbish "is not a test-mode credential" sends them to rotate a key that was never
    a key. ==Each refusal answers its own question.==
    """


class LiveCredentialRefusedError(CredentialError):
    """A money credential was live, and this gateway has operations nobody has ever run for real.

    ==The refusal is not a claim about the KEY. It is a claim about the CODE that would use it.==
    ``integrations/stripe.py`` records that its gateway is *"NOT verified against live Stripe...
    exercised only with a stubbed transport"*. ``integrations/mercadopago.py`` is blunter still:
    *"No Mercado Pago account exists for this project, so nothing here has ever opened a checkout,
    taken a real payment, or issued a real refund."*

    Until this class, ==nothing enforced any of that==. The title was a filename and the warnings
    were prose. An operator pasting an ``sk_live_`` key into ``credentials set`` got a product that
    charged a real guest's real card through code that had never once spoken to Stripe — and every
    status code would have said success. "LIVE is not wired" sounds like an absence; the reality was
    *present and unverified*, which is the worse of the two, because it needed nobody to build it.
    It needed only that nobody had refused it.

    .. rubric:: ==The question this asks — and the one it USED to ask==

    It used to ask *"is this key a TEST key?"*, and answered by requiring an ``sk_test_`` prefix.
    ==That question has only one possible answer for ever==, because no amount of testing changes a
    prefix: a product that refuses live keys is a product that cannot take money, and the guard was
    therefore permanent by construction. The prefix was standing in for the fact that actually
    mattered, and ==a stand-in cannot be discharged by evidence — only overruled==, which is how a
    guard ends up deleted in a hurry by whoever needs to ship.

    It now asks the fact directly: ==*has this provider's gateway been EXERCISED against the real
    API, and for WHICH operations?*== (:func:`live_verifications`). An operation nobody has run is
    refused exactly as before — so an unverified provider is still refused, arrived at honestly —
    but the refusal can now be **retired by evidence** instead of only by fiat.

    .. rubric:: ==Refused, not warned — because the danger must not arrive by OMISSION==

    There is no flag, no ``allow_live=`` argument and no environment escape hatch, for the same
    reason :func:`resolve_money_credential` has no ``instance_default``: there must be nothing to
    pass. A warning is ignorable by doing nothing, and doing nothing is exactly how a live key would
    arrive here — nobody DECIDES to charge through an unverified adapter, they simply paste the key
    they had. A refusal cannot be reached by inattention.

    ==And the evidence cannot arrive by omission either.== Lifting this is not an edit to a boolean:
    it is a :class:`LiveVerification` record naming the operation, the date and what was observed,
    ONE PER OPERATION, produced by running the harness in ``apps/server/tests/live/``. Every
    operation the gateway performs needs one, because ==a gateway verified by halves is a gateway
    whose other half moves somebody's money unseen==.
    """


class StaleVerificationError(CredentialError):
    """A LIVE credential was about to run an operation whose evidence no longer describes the code.

    ==Distinct from :class:`LiveCredentialRefusedError`, and the difference is WHEN.== That one is
    the door: it refuses to STORE a live credential. This one is the road: the credential was stored
    legitimately, under evidence that was current at the time, and the gateway has been edited
    since — so what would run NOW is code nobody has exercised, against somebody's real card.

    ==The write-time door could never have caught this.== It ran once, correctly, months ago.
    """


class MalformedCredentialError(CredentialError):
    """A stored credential decrypted to valid JSON that is NOT an object of field → value.

    ==Distinct from :class:`IncompleteCredentialError`.== That is a credential-SHAPED object missing
    a required field; this is a payload whose whole shape is wrong — a number, a string, an array,
    ``null`` — so it has no fields to read at all. It can only arrive from OUTSIDE the current write
    path: a row written before the CLI's value-shape guard existed, a ciphertext corrupted into
    still-valid JSON, or a writer that bypassed the service. The resolver raises this rather than
    letting ``.items()`` throw a bare ``AttributeError`` — a raw crash on the money path, where a
    guest's card may already have been charged, is the worst place to surface a bug as a stack
    trace.
    """


def credential_class(provider: CredentialProvider) -> CredentialClass:
    """MONEY or INFRA. ==Exhaustive: a new provider does not type-check without an answer.==

    The ``assert_never`` is the load-bearing part. Adding a payment processor and forgetting to
    classify it would otherwise leave it inheriting whatever the default branch happened to be — and
    if that branch were INFRA, the new processor would silently gain an instance-default fallback: a
    business charging into the operator's account, shipped by omission.
    """
    match provider:
        case CredentialProvider.STRIPE | CredentialProvider.MERCADO_PAGO:
            return CredentialClass.MONEY
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            return CredentialClass.INFRA
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def required_fields(provider: CredentialProvider) -> frozenset[str]:
    """The fields without which this provider cannot do its job. ==Exhaustive, for the same
    reason.==

    Extra fields are allowed and preserved (an SMTP port, a publishable key, a base-URL override):
    each provider's consumer knows its own optional shape. What cannot be allowed is a MISSING
    required one, because that produces a credential which looks configured and is not.
    """
    match provider:
        case CredentialProvider.STRIPE:
            # The webhook secret is not optional: without it the charge's confirmation cannot be
            # verified, and an unverified confirmation is never applied.
            return frozenset({"secret_key", "webhook_secret"})
        case CredentialProvider.MERCADO_PAGO:
            return frozenset({"access_token", "webhook_secret"})
        case CredentialProvider.SMTP:
            return frozenset({"host", "from_addr"})
        case CredentialProvider.WHATSAPP:
            return frozenset({"base_url", "instance", "api_key"})
        case CredentialProvider.SMS:
            return frozenset({"account_sid", "auth_token", "from_number"})
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


class GatewayOperation(StrEnum):
    """One ACT a money gateway performs against its provider's API. ==Verified one at a time.==

    .. rubric:: ==Why the unit of verification is the OPERATION and not the provider==

    Because the two acts cost different things to prove, and a single ``stripe: verified`` flag
    would have to lie about one of them.

    * a Checkout Session can be created, read back and expired against the real API for **nothing**
      — no card, no charge, no money moves at any point;
    * a refund cannot be proved without a real charge to refund. The evidence has a PRICE, and
      somebody has to decide to pay it.

    So a run that exercises checkout says nothing whatever about refund — and ==refund is the path
    whose failure lands on a guest who has ALREADY PAID==, which is the worst place in this system
    for a claim to be optimistic. One flag for both would record the cheap evidence and quietly
    extend it to cover the expensive question.

    ==This enum is the domain's, not the gateway module's, and that is deliberate.== The
    verification registry is read on the credential WRITE path, and this module cannot import
    ``integrations.money`` — that module imports THIS one, so the reverse edge would be a cycle.
    What the door needs from ``integrations`` therefore arrives as an ARGUMENT
    (``current_implementations``, see :func:`verified_operations`) rather than as an import: the
    dependency is inverted, not dropped.

    ``tests/test_credential_mode_guard.py`` still ties the enum to the protocol from outside: it
    walks :class:`~aethercal.server.services.payments.PaymentGateway` and asserts every operation
    the protocol declares has a member here — so a THIRD operation (F5's partial refund, a capture)
    cannot be added to the gateway without arriving as unverified and re-closing the door.
    """

    CHECKOUT = "checkout"
    """``create_checkout_session`` — opening a hosted checkout. ==Provable at zero cost.=="""

    REFUND = "refund"
    """``refund`` — sending a guest's money back. ==Only provable by charging somebody first.=="""


class ProviderMode(StrEnum):
    """Which of a provider's two worlds a verification happened in. ==They are not one system.==

    A provider's test mode is a *different backend*: different keys, no card networks, different
    fraud rules, different webhook signing secrets, and no money. A round-trip there proves the
    request shape and the transport — real, and worth having — and it proves ==nothing whatever
    about live mode==, which is where the money is.
    """

    TEST = "test"
    """Exercised against the provider's sandbox. Free, and it authorises nothing about live."""

    LIVE = "live"
    """Exercised against the provider's real, money-moving backend."""


def authorises_live_credentials(mode: ProviderMode) -> bool:
    """May evidence gathered in ``mode`` open the door to a LIVE credential? ==Exhaustive.==

    ``assert_never``, like :func:`credential_class`: a third mode (a provider's "sandbox" tier, a
    regional staging environment) does not type-check until somebody has said whether evidence from
    it is worth real money. ==The dangerous default is "yes"==, so this makes having no answer
    impossible rather than merely discouraged.
    """
    match mode:
        case ProviderMode.LIVE:
            return True
        case ProviderMode.TEST:
            return False
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class LiveVerification:
    """The record of ONE operation exercised against the provider's REAL API, in ONE mode.

    ==A boolean is a claim; this is a claim with its receipt attached.== The thing being asserted —
    "this code has spoken to the real provider and behaved" — is a historical fact about a run that
    somebody performed on a day, and a bare ``True`` records none of it. Six months on, the question
    that matters is not *is it verified?* but *what exactly was run, when, in which mode, and did it
    cover the operation I am about to trust?*, and only the evidence answers that.

    It also changes what flipping the switch COSTS. A boolean is flipped by whoever is impatient; a
    record has to be written, and writing "checkout: created cs_live_…, expired it, no money moved"
    requires having done it. ==The friction is the feature==: this is the exact edit that stands
    between the product and somebody's real money.

    .. rubric:: ==Why :attr:`mode` exists, and the trap it closes==

    The first cut recorded the date and the observation but nothing structured about WHICH MODE the
    run happened in — so ==evidence gathered for free in TEST mode would have opened the door to a
    LIVE credential==. That is not hypothetical: verifying in test mode is the cheap and obvious
    thing to do, and every incentive points at it. The guard would have gone on looking like a guard
    while quietly answering a question it no longer asked — the same failure as the ``sk_test_``
    prefix, one level deeper and harder to see.

    ==Note the asymmetry, which is the whole point.== LIVE evidence authorises a live credential;
    TEST evidence does not. Recording a TEST run is still worth doing — it is honest, and says the
    transport and the request shape work — it simply buys nothing that the money guard is willing to
    spend. ``verified_on``/``evidence`` say *what happened*; this says *what it is worth*.
    """

    operation: GatewayOperation
    """Which act was exercised. ==One record per act== — never one record meaning "the provider"."""

    mode: ProviderMode
    """==Which world it was exercised in.== Only :attr:`ProviderMode.LIVE` authorises a live key."""

    implementation: str
    """==WHICH CODE was exercised== — ``integrations.money.implementation_fingerprint``.

    Without it a verification outlives the thing it verified: rewrite ``StripeGateway.refund``
    tomorrow and the register still says "verified", about code nobody has ever run.

    ==The comparison happens IN THE DOOR, at write time.== :func:`verified_operations` is handed the
    fingerprints of the code that would run right now and counts a record only while the two agree,
    so an edit to the method ==invalidates its verification== and the next live credential is
    refused — in production, not merely in the suite.

    .. rubric:: ==It used to be checked only by a test, and that was the whole defect==

    The re-computation lived in ``tests/test_credential_mode_guard.py`` and nowhere else, because
    this module may not import ``integrations.money``. So the register went stale in the one place
    it mattered: the suite would turn red on the next run, while ``verified_operations()`` — the
    function the door actually consults — went on authorising a live credential against an
    implementation nobody had ever exercised. ==A guard enforced only by its own test is a guard the
    product does not have.== The import direction was a real constraint and it was never a reason to
    leave the check out of the decision: the fingerprints are INJECTED instead
    (``current_implementations``), and the layer that can see both sides supplies them.

    It stays a plain string here for the same layering reason it always was — this module cannot
    compute it, so it stores what it was told and compares it against what it is handed.
    """

    verified_on: date
    """The day the harness was run against the real API."""

    evidence: str
    """What was actually observed: the calls made, the identifiers returned, the money NOT moved."""


def gateway_operations(provider: CredentialProvider) -> frozenset[GatewayOperation]:
    """Which acts this provider's gateway performs. ==DERIVED from the credential class.==

    Every MONEY provider gets the same set because they all implement the same
    :class:`~aethercal.server.services.payments.PaymentGateway` protocol — the operations are a
    property of that seam, not of the individual provider. Deriving it means a new payment processor
    inherits the full list of things it must prove, rather than an empty one somebody forgot to fill
    in.

    INFRA providers have no gateway at all, so the answer is the empty set — and that is what makes
    :func:`unverified_operations` vacuously empty for them, which is the correct reading: an SMTP
    relay has nothing to verify against a payment API because it never talks to one.
    """
    match credential_class(provider):
        case CredentialClass.MONEY:
            return frozenset(GatewayOperation)
        case CredentialClass.INFRA:
            return frozenset()
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def live_verifications(provider: CredentialProvider) -> tuple[LiveVerification, ...]:
    """What has ACTUALLY been exercised against this provider's real API. ==Exhaustive.==

    ==This is the fact the money guard is now founded on==, and it is a register of history rather
    than a policy: each entry is something somebody ran, on a day, and wrote down what came back.

    A tuple of records rather than a mapping keyed by operation, so there is no key that can
    disagree with the record it points at — the operation a verification is ABOUT is a field of the
    verification, and it is stated once.

    ``assert_never``, exactly as in :func:`credential_class` and :func:`required_fields`: a third
    payment processor does not type-check until somebody has said what has been run against it, and
    the only honest answer on the day it is added is ``()``.
    """
    match provider:
        case CredentialProvider.STRIPE:
            # ==NOTHING. `StripeGateway` has still only ever spoken to a stubbed transport.==
            #
            # The harness that can change this line is `tests/live/test_stripe_live_checkout.py`,
            # and ONLY running it may: `CHECKOUT` goes here when somebody has watched a real
            # Checkout Session be created, read back and expired against api.stripe.com. `REFUND`
            # needs a real charge to refund, which is a decision with a price attached — see
            # `docs/byok-credentials.md`.
            return ()
        case CredentialProvider.MERCADO_PAGO:
            # No Mercado Pago account exists for this project, so neither operation has ever run.
            # Nothing here is pending a harness — it is pending an ACCOUNT.
            return ()
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            # No payment gateway, nothing to verify against one. See `gateway_operations`.
            return ()
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def _names_current_code(
    record: LiveVerification, current_implementations: Mapping[GatewayOperation, str]
) -> bool:
    """Does this record describe the code that would run TODAY?

    ``.get`` rather than ``[]`` deliberately: an operation absent from the mapping compares unequal,
    so it reads as "not verified". ==Every way of getting this argument wrong is restrictive==, and
    that is the direction a money guard must fail in.
    """
    return current_implementations.get(record.operation) == record.implementation


def stale_verifications(
    provider: CredentialProvider,
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> tuple[LiveVerification, ...]:
    """Records that name an implementation the tree no longer contains. ==Evidence about ghosts.==

    A verification is a claim about CODE, not about a provider's name: rewrite
    ``StripeGateway.refund`` and its record describes a method that no longer exists. This names
    those records — for the refusal message, which owes the operator the difference between *nobody
    ran this* and *somebody ran something else*, and for the suite, which asserts the real register
    holds none.

    Mode-agnostic on purpose: a stale TEST record is just as stale, and calling it anything else
    would make the suite's staleness check depend on which mode somebody happened to record.
    """
    return tuple(
        record
        for record in live_verifications(provider)
        if not _names_current_code(record, current_implementations)
    )


def verified_operations(
    provider: CredentialProvider,
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> frozenset[GatewayOperation]:
    """The operations whose evidence ==authorises a LIVE credential.== Derived from the register.

    ==Not simply "operations with a record".== A record earns its place here only if BOTH are true:

    * it was gathered in a mode that can pay for a live key (:func:`authorises_live_credentials`) —
      a TEST-mode round-trip is real evidence about the transport and buys nothing here;
    * it names the code that would ACTUALLY RUN (:func:`stale_verifications`) — an edit to the
      gateway method retires its own verification.

    Both filters live HERE, in the function the door consults, and not at the call sites: there is
    exactly one place that decides what evidence is worth, so no caller can forget to apply it.

    .. rubric:: ==Why ``current_implementations`` is a required argument with NO DEFAULT==

    This module may not import ``integrations.money`` (that module imports it, so the reverse edge
    is a cycle), and the fingerprint of a gateway method can only be computed there. The
    constraint is real; ==leaving the comparison out of the decision because of it was not.== That
    is what happened: the staleness check lived in ``tests/test_credential_mode_guard.py`` alone, so
    the evidence expired in the suite and went on authorising live credentials in production.

    So the fact is INJECTED by the layer that can see both sides
    (``integrations.money.current_gateway_implementations``), and the parameter has **no default**
    on purpose — a default is exactly how a caller forgets:

    * ``{}`` as a default would mark every record stale, so the door could never open at all and the
      register would be decorative — the "permanent by construction" guard this design replaced;
    * a default that skipped the check would restore the defect verbatim.

    ==There is nothing to pass by accident and nothing to omit.== A caller that supplies a partial
    or empty mapping gets a STRICTER door, never a laxer one; the only way to widen it is to supply
    fingerprints that match the register, which is a deliberate forgery rather than an oversight.
    """
    return frozenset(
        record.operation
        for record in live_verifications(provider)
        if authorises_live_credentials(record.mode)
        and _names_current_code(record, current_implementations)
    )


def unverified_operations(
    provider: CredentialProvider,
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> frozenset[GatewayOperation]:
    """What this gateway DOES, minus what has been seen working. ==The gap the guard defends.==

    Non-empty means: there is an act this product would perform with a live credential that has
    never once been performed for real — or was, against code that has since changed. Empty means
    every act the gateway can take has a receipt, and the receipt describes what would run.
    """
    return gateway_operations(provider) - verified_operations(
        provider, current_implementations=current_implementations
    )


class MoneyDirection(StrEnum):
    """Which WAY the money moves in an operation. ==The two are not equally safe to refuse.=="""

    TAKES_PAYMENT = "takes_payment"
    """Out of a guest's account and into the business's. Refusing costs a booking."""

    RETURNS_PAYMENT = "returns_payment"
    """Back to a guest who has ALREADY PAID. ==Refusing strands somebody else's money.=="""


def money_direction(operation: GatewayOperation) -> MoneyDirection:
    """Which way ``operation`` moves money. ==Exhaustive, and it decides a refusal policy.==

    ``assert_never``, like :func:`credential_class`: a third operation (F5's partial refund, a
    capture) does not type-check until somebody has said which way it moves money — and therefore
    whether refusing it protects a guest or robs one.
    """
    match operation:
        case GatewayOperation.CHECKOUT:
            return MoneyDirection.TAKES_PAYMENT
        case GatewayOperation.REFUND:
            return MoneyDirection.RETURNS_PAYMENT
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def blocks_on_stale_evidence(operation: GatewayOperation) -> bool:
    """May a live credential be REFUSED at use time when its evidence has gone stale?

    .. rubric:: ==The asymmetry, and the measurement behind it==

    The write-time door refuses everything equally, and it can afford to: nothing is at stake but a
    configuration step. At USE time the two directions have different worst cases, so one policy
    would have to be wrong about one of them.

    * ==**TAKES_PAYMENT → refuse.**== Charging a guest through an adapter nobody has exercised is
      the exact act this whole design exists to prevent, and the failure is silent: every status
      code says success while the money may have gone nowhere useful. Refusing costs the business
      new bookings — visible immediately, and recoverable at ZERO COST by re-running the free
      checkout harness (or reverting the deploy). Nobody's money is stranded by the refusal.

    * ==**RETURNS_PAYMENT → never refuse.**== Blocking a refund does not prevent the harm; it
      CAUSES it. The harm being guarded against is "the guest's money does not come back", and a
      block produces exactly that outcome, with certainty, indefinitely — the refund intent stays
      queued and retries for ever. Letting an unexercised refund run risks the same outcome and may
      well avoid it. ==A guard whose failure mode is identical to the harm, but guaranteed, is not a
      guard.== So the refund proceeds and the caller is handed a reason to ALARM.

    ==The honest limit of that second branch, stated rather than implied==: an unexercised refund
    does run against real money, and this accepts that. What makes it acceptable is that its
    realistic failure is LOUD — the gateway raises, the outbox retries, the intent goes dead-letter
    with an alert — and not a silent loss. The quiet, expensive failure lives on the CHARGING side,
    which is the side that is refused.

    ``assert_never`` over the direction rather than over the operation, so a new operation inherits
    the reasoning through its direction instead of getting an answer somebody typed in a hurry.
    """
    match money_direction(operation):
        case MoneyDirection.TAKES_PAYMENT:
            return True
        case MoneyDirection.RETURNS_PAYMENT:
            return False
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def is_live_credential(provider: CredentialProvider, secrets: Mapping[str, str]) -> bool:
    """Does this credential address the provider's REAL, money-moving backend?

    ==Read off the DECLARED test prefixes, never off the enforced map.== The enforced one relaxes as
    evidence accumulates, so a verified provider would answer "nothing is live", which is the
    opposite of the truth and would switch the use gate off at precisely the moment it starts to
    matter. This is the same reading :func:`_validate` does at the door, asked from the other side.

    An INFRA provider declares no prefixes, so the answer is ``False``: an SMTP relay has no live
    mode to be in.
    """
    return any(
        not str(secrets.get(field, "")).startswith(prefix)
        for field, prefix in declared_test_mode_prefixes(provider).items()
    )


def authorise_live_use(
    provider: CredentialProvider,
    operation: GatewayOperation,
    secrets: Mapping[str, str],
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> str | None:
    """==The gate on USE.== May this stored credential perform ``operation`` right now?

    .. rubric:: ==Why a second gate exists at all==

    :func:`store_credential` is a gate on WRITING. It answers once, on the day somebody types the
    key, and it is then over — so a gateway edited afterwards keeps moving real money on the
    strength of a verification that no longer describes it. ==The evidence expires; the credential
    does not.== Nothing re-asked the question, and the row is read by a refund six weeks later on a
    Sunday.

    .. rubric:: What it returns, and why it is two-faced on purpose

    * ``None`` — go ahead. Either the credential is test-mode (no live money, never gated), or the
      operation's evidence is current;
    * **raises** :class:`StaleVerificationError` — when :func:`blocks_on_stale_evidence` says this
      operation may be refused. Raising rather than returning a verdict, for the reason
      :func:`resolve_money_credential` raises: a boolean is one hurried ``if`` away from being
      ignored, and what it guards is a stranger's card;
    * **returns a reason string** — when the evidence is missing but refusing would be worse than
      proceeding (the refund direction). ==The caller MUST alarm it.== It is handed back rather than
      logged here so the alarm carries the caller's context — which payment, which business.

    ==The decision of WHICH of those two happens is not the caller's.== It lives in
    :func:`blocks_on_stale_evidence`, exhaustively, so no call site can quietly choose to be lenient
    with the charging path or brutal with the refund one.

    ==Every way of getting ``current_implementations`` wrong is restrictive on the charging side and
    noisy on the refund side==, never silent — the same property :func:`verified_operations`
    guarantees, and the reason it has no default here either.
    """
    if not is_live_credential(provider, secrets):
        return None  # test mode: no real money, and the guard was never about the key's shape
    if operation in verified_operations(provider, current_implementations=current_implementations):
        return None

    stale = stale_verifications(provider, current_implementations=current_implementations)
    named = sorted(record.operation.value for record in stale if record.operation is operation)
    reason = (
        f"the {provider.value} gateway's `{operation.value}` has no CURRENT evidence of having "
        "been run against the real provider in LIVE mode"
        + (
            ", and the record it had names code that has since CHANGED"
            if named
            else " — no record authorises it"
        )
        + ". A live credential was stored while the evidence was good; the gateway has been edited "
        "since, so what would run now is code nobody has exercised, against a real card. Re-run "
        "the harness in `apps/server/tests/live/` for that operation and record the new "
        "fingerprint. (No key or value is shown — it is a secret, stale evidence or not.)"
    )
    if blocks_on_stale_evidence(operation):
        raise StaleVerificationError(reason)
    return reason


def declared_test_mode_prefixes(provider: CredentialProvider) -> Mapping[str, str]:
    """field → the prefix that PROVES test mode. ==The DECLARATION: an allowlist, and exhaustive.==

    This is what each provider's own scheme says, and it never changes with what has been verified.
    ==What is ENFORCED at any moment is :func:`required_test_mode_prefixes`==, which reads this one
    through the verification register.

    Keeping the two apart is what stops the derived-rule tests going vacuous: they assert *every
    money provider declares a prefix, and the field it names is one the provider requires*, and they
    must keep asserting it after a provider is verified and stops being checked. A test that walked
    the ENFORCED map would silently start passing over an empty dict — proving nothing, loudly
    green.

    .. rubric:: ==Why a required prefix and not a list of forbidden ones==

    The obvious spelling is "reject ``sk_live_``". It is a photograph of what we happened to know on
    the day it was written, and it fails open on everything else: Stripe's restricted live keys
    (``rk_live_``), whatever prefix Stripe introduces next, a publishable key pasted by mistake, a
    truncated paste, an ``access_token`` from the wrong account. Every one of those is "not
    ``sk_live_``", so every one would be stored as though it had been checked — and the mistakes are
    likelier than the deliberate act. Requiring the TEST prefix inverts that: anything not provably
    test-mode is refused, which is the only direction that fails closed.

    .. rubric:: ==Exhaustive, so a new payment provider cannot arrive without an answer==

    ``assert_never``, exactly as in :func:`credential_class` and :func:`required_fields`. A third
    processor does not type-check until somebody has said which prefix proves it is in test mode —
    and ``tests/test_credential_mode_guard.py`` asserts, by walking the enum, that every provider
    classified MONEY declares one and that the field it names is one the provider REQUIRES (a guard
    on an optional field is skipped by leaving the field out).

    The INFRA providers return an empty mapping, and that is a decision rather than a gap: an SMTP
    host, a WhatsApp instance or a Twilio SID carry no test/live distinction in the value, so there
    is nothing here to read. Inventing one would refuse every legitimate mail server on the
    internet.
    """
    match provider:
        case CredentialProvider.STRIPE:
            # Stripe's own scheme: `sk_test_…` in test mode, `sk_live_…` in live mode.
            return {"secret_key": "sk_test_"}
        case CredentialProvider.MERCADO_PAGO:
            # Mercado Pago's own scheme: `TEST-…` for the sandbox, `APP_USR-…` in production.
            return {"access_token": "TEST-"}
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            return {}
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


KEY_BODY_CHARACTERS = re.compile(r"\A[A-Za-z0-9_-]+\Z")
"""What may follow a key's prefix. ==One unbroken token: no space, no quote, no brace, no newline.==

.. rubric:: Why one shared alphabet and not a per-provider one

The tempting spelling is each provider's exact alphabet — Stripe's keys are base62, Mercado Pago's
tokens are digits and hyphens. ==That would be a specification we do not own.== A third party can
widen its own format without telling us, and the cost of being wrong is asymmetric: refusing a
GENUINE key locks a business out of taking money, while admitting a well-formed impostor costs a
``401`` on the first call and no money moves. So this is deliberately the loose reading — the
characters a key of ANY of these schemes is made of.

What it does catch is what a bad paste actually looks like: a wrapped terminal that inserted a
space or a newline, a value carrying its surrounding quotes, a fragment of JSON, a field that is
plainly prose. Those are the real failure modes, and none of them survives this.
"""


@dataclass(frozen=True, slots=True)
class KeyFamily:
    """What makes a value a RECOGNISABLE key of its provider: the prefix, and the shape of the rest.

    ==A prefix on its own was never enough, and the error message said so before the code did.== The
    check was ``value.startswith(prefixes)``, and its refusal promised to catch "a truncated paste"
    — while ``"sk_live_"``, typed alone, sailed through and was stored as a payment credential.
    ==What it certified was not what it measured==: the contract was written for a check that did
    not exist. This is that check.
    """

    prefixes: tuple[str, ...]
    """Every prefix that starts a key of this provider. ==An allowlist== — see
    :func:`credential_key_families` for why it is not a list of forbidden ones."""

    minimum_body: int
    """How much must follow the prefix before this can be a key at all.

    ==Deliberately far BELOW the shortest key any of these providers issues.== It is a floor to
    catch a stub, not a fingerprint of a format: set it near the real length and the day a provider
    shortens its scheme, this door refuses genuine keys and a business cannot charge. Set it here
    and it still refuses the prefix alone, a paste truncated to a handful of characters, and an
    empty-ish value — the shapes that are unambiguously not a key.
    """

    def unrecognised(self, value: str) -> str | None:
        """Why ``value`` is not a key of this provider, or ``None`` if it might be one.

        ==Returns the RULE that was broken, never anything measured off the value.== "It is 6
        characters long" would be a fact about a secret, printed into the operator's terminal and
        their shell history; a value refused here can still be a real key that was truncated, so its
        length is not ours to publish. Naming the rule is enough to fix the input.
        """
        for prefix in self.prefixes:
            if value.startswith(prefix):
                body = value[len(prefix) :]
                if len(body) < self.minimum_body:
                    return (
                        "it stops at (or just after) the prefix — there is not enough after it for "
                        "this to be a key. A prefix on its own is not a credential"
                    )
                if not KEY_BODY_CHARACTERS.match(body):
                    return (
                        "what follows the prefix is not a single unbroken token — it carries a "
                        "space, a line break, a quote or some other character no key contains, "
                        "which is what a copy-paste out of a wrapped terminal or a JSON snippet "
                        "looks like"
                    )
                return None
        return "it does not begin with any prefix this provider's keys begin with"


def credential_key_families(provider: CredentialProvider) -> Mapping[str, KeyFamily]:
    """field → what a RECOGNISABLE key for it looks like. ==Always enforced, forever.==

    .. rubric:: ==The hole this closes: the type check used to evaporate on success==

    There was only ever one check on the SHAPE of a payment key, and it was the TEST-mode prefix —
    so the moment a provider became fully verified, :func:`required_test_mode_prefixes` returned
    ``{}`` and ==nothing looked at ``secret_key`` at all any more==. A truncated paste, a webhook
    secret dropped in the wrong field, or plain rubbish would have been stored without a murmur.
    ==The validation vanished exactly when the system started moving real money==, which is the
    worst possible moment to relax one.

    Two questions were tangled together, and they are now separate:

    * **what KIND of thing is this?** — permanent, and answered here. A Stripe secret key is
      ``sk_test_…`` or ``sk_live_…``; nothing else is one, whatever the register says;
    * **is this the right MODE for what has been proved?** — temporary, and answered by
      :func:`required_test_mode_prefixes`, which relaxes as evidence accumulates.

    Still an ALLOWLIST, for the reason it always was: a restricted key (``rk_live_``), a publishable
    key, a prefix Stripe introduces next year or a fat-fingered paste is refused because it is not
    on the list — never admitted because nobody thought to forbid it.

    .. rubric:: ==What this can decide, and what it CANNOT — stated because the message used to
       overstate it==

    It decides SHAPE: the prefix, that something of key-like length follows it, and that what
    follows is one unbroken token (:class:`KeyFamily`). That is enough to refuse the prefix typed on
    its own, a paste truncated to a stub, and a value contaminated by a line break or a quote.

    ==It cannot decide whether a well-formed key is genuine, current, or yours.== A key from another
    Stripe account, a rotated one, a key truncated by three characters — all of them are the right
    shape, and no amount of local inspection separates them from the real thing. Only an
    authenticated call to the provider can, and this door deliberately makes none: a credential
    write would then depend on the provider being reachable, and an outage would stop a business
    configuring itself. So the limit is REAL, and the refusal now says so rather than claiming to
    catch "a key from another account" — a promise the code never kept.
    """
    match provider:
        case CredentialProvider.STRIPE:
            # `sk_test_…` / `sk_live_…`, then base62. The floor is well under the shortest secret
            # key Stripe has ever issued (its legacy format already carried 24 characters).
            return {"secret_key": KeyFamily(prefixes=("sk_test_", "sk_live_"), minimum_body=12)}
        case CredentialProvider.MERCADO_PAGO:
            # `TEST-…` / `APP_USR-…`, then hyphen-separated digits and letters, far longer than 12.
            return {"access_token": KeyFamily(prefixes=("TEST-", "APP_USR-"), minimum_body=12)}
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            # No test/live distinction in the value and no house format either: an SMTP host is
            # whatever the business's mail provider calls it.
            return {}
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def required_test_mode_prefixes(
    provider: CredentialProvider,
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> Mapping[str, str]:
    """What the door ENFORCES right now. ==DERIVED from what has been verified, not declared.==

    ==This relaxes; :func:`credential_key_families` never does.== What evidence lifts is the
    restriction to the TEST variant — never the requirement that the value be a recognisable key of
    that provider at all.

    One sentence, and the whole of the money guard reads off it: ==*a provider whose gateway still
    has an unexercised operation must present a provably TEST-mode credential; a provider whose
    every operation has a receipt may present a live one.*==

    .. rubric:: ==Why ALL of them, and not just the one about to run==

    A stored credential is not scoped to an operation. It sits in the row that ``refund`` will read
    six weeks from now, on a Sunday, for a guest who has already paid. This door authorises
    everything the gateway can do, so it must be satisfied about everything the gateway can do —
    ==an aggregate that lets ONE verified operation open the door would be authorising the others by
    silence==, which is the shape of the original defect wearing a newer word.

    The practical consequence, stated rather than discovered: verifying Stripe's checkout (free) is
    ==not enough on its own== to accept a live Stripe key, because ``refund`` remains unexercised.
    That is the honest reading of the evidence, and it is exactly what a single "Stripe verified"
    flag would have hidden.

    .. rubric:: The three degenerate answers, and why each is right

    * **unverified provider** → the declared prefixes. The pre-existing refusal, unchanged;
    * **fully verified provider** → ``{}``. Nothing to check: a live key is as legitimate as a test
      one, which is the entire point of having verified it. A test-mode key still passes — it simply
      is not required any more;
    * **INFRA provider** → ``{}`` by both routes at once (no gateway operations, no declared
      prefixes). It was never subject to a mode and still is not.

    ``current_implementations`` travels through to :func:`verified_operations`, which is where it is
    explained and where the "no default" rule is enforced. It is threaded rather than fetched
    because this module cannot fetch it — see that function.
    """
    if unverified_operations(provider, current_implementations=current_implementations):
        return declared_test_mode_prefixes(provider)
    return {}


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedCredential:
    """A decrypted credential, with the record of WHERE it came from. ==Its ``repr`` is
    redacted.=="""

    provider: CredentialProvider
    source: CredentialSource
    secrets: Mapping[str, str]

    def __repr__(self) -> str:
        """Names the provider, the source and the FIELD NAMES — never a value.

        ``logger.info("resolved %s", credential)`` is the likeliest way a payment key ever reaches a
        log file, and it is one careless format string away at all times. A dataclass's generated
        ``repr`` would print the lot. This one cannot.
        """
        fields = ", ".join(sorted(self.secrets))
        return (
            f"ResolvedCredential(provider={self.provider.value}, source={self.source.value}, "
            f"secrets=<redacted: {fields}>)"
        )


def _validate(
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    *,
    current_implementations: Mapping[GatewayOperation, str],
) -> dict[str, str]:
    """Refuse a half-configured credential — or a LIVE one — AT THE DOOR.

    ==The door, and not the callers.== Every write of this table goes through
    :func:`store_credential`, which goes through here, so a second writer (an admin route, an
    importer, a fixture) inherits both refusals rather than having to remember them. Gating the
    caller instead of the funnel is how the CLI ends up with a check the admin UI does not have.

    ==And the live check is the DOOR's, not the suite's.== The staleness half of it used to live
    only in ``tests/test_credential_mode_guard.py``, so a rewritten gateway method expired its
    verification in CI while this function went on storing live credentials against it.
    ``current_implementations`` is what closes that: the fact the comparison needs arrives as an
    argument, and :func:`verified_operations` explains why it has no default.

    The three refusals run in this order deliberately, and each keeps answering its OWN question: a
    credential missing a field is INCOMPLETE whatever mode its other fields are in, and reporting
    that as a live-key refusal would send the operator off to rotate a key that was never the
    problem.
    """
    present = {key: value for key, value in secrets.items() if str(value).strip()}
    missing = sorted(required_fields(provider) - present.keys())
    if missing:
        raise IncompleteCredentialError(
            f"the {provider.value} credential is missing {', '.join(missing)}.\n"
            "\n"
            "A credential that exists but cannot finish its job is worse than none at all: it "
            "looks configured, and it fails at the moment it is used — which, for a payment "
            "provider, is the moment a guest's money has already left their card.\n"
            "\n"
            f"Required for {provider.value}: {', '.join(sorted(required_fields(provider)))}."
        )

    # ==FIRST, and permanently: is this a key of this provider at all?== This check does NOT relax
    # when the register fills up. It used to be welded to the mode guard below, so it evaporated the
    # day a provider became fully verified — the one moment it mattered most.
    for field, family in credential_key_families(provider).items():
        problem = family.unrecognised(str(present[field]))
        if problem is not None:
            raise UnrecognisedCredentialError(
                f"the {provider.value} `{field}` is not a recognisable {provider.value} key: "
                f"{problem}.\n"
                "\n"
                f"It must begin with one of: {', '.join(family.prefixes)}, followed by at least "
                f"{family.minimum_body} more characters, all of them letters, digits, `-` or `_`. "
                "That refuses a publishable or restricted key, a webhook secret dropped in the "
                "wrong field, the prefix typed on its own, a paste truncated to a stub, and a "
                "value carrying a line break or a quote.\n"
                "\n"
                "==What it cannot tell you, said plainly==: whether a well-formed key is genuine, "
                "current, or belongs to your account. Only an authenticated call to the provider "
                "decides that, and this door makes none — so a key of the right shape from the "
                "WRONG account is stored here and fails when somebody tries to pay.\n"
                "\n"
                "==This check never relaxes.== Verifying the gateway against the real API lifts "
                "the restriction to TEST-mode keys; it does not make a non-key acceptable.\n"
                "\n"
                "(The value is not shown — it is a secret, wrong shape or not. Neither is anything "
                "measured off it: a refused value can still be a real key that was truncated.)"
            )

    for field, prefix in required_test_mode_prefixes(
        provider, current_implementations=current_implementations
    ).items():
        if not str(present[field]).startswith(prefix):
            # ==Names the FIELD and the PROVIDER — both fixed literals we control — and NEVER the
            # value.== A live key is the most sensitive thing this system is ever handed, and
            # refusing it does not make it less secret; echoing it (or even the prefix it failed on)
            # would put it in the operator's terminal, their shell history and the CLI's stderr.
            outstanding = unverified_operations(
                provider, current_implementations=current_implementations
            )
            unverified = ", ".join(sorted(op.value for op in outstanding))
            # ==Name the operations whose ONLY evidence is test-mode, separately.== Being told
            # "refund is unverified" right after watching a green test-mode run of refund reads as a
            # bug in the guard; being told the evidence exists but was gathered where there is no
            # money is the real state of the world, and it sends somebody to the right fix.
            test_only = sorted(
                record.operation.value
                for record in live_verifications(provider)
                if record.operation in outstanding and not authorises_live_credentials(record.mode)
            )
            # ==And the same courtesy for evidence that has gone STALE==, which reads even more like
            # a broken guard: the operator ran the harness in LIVE mode, wrote the record, and is
            # refused anyway — because the gateway method was edited afterwards, so the evidence
            # describes code that no longer exists. Only naming it sends them to the re-run.
            stale = sorted(
                record.operation.value
                for record in stale_verifications(
                    provider, current_implementations=current_implementations
                )
                if record.operation in outstanding and authorises_live_credentials(record.mode)
            )
            raise LiveCredentialRefusedError(
                f"the {provider.value} `{field}` is not a test-mode credential, and the "
                f"{provider.value} gateway still performs operations that have NEVER been run "
                f"against the real provider in LIVE mode: {unverified}.\n"
                "\n"
                + (
                    f"({', '.join(test_only)} HAS been exercised, but in TEST mode — a different "
                    "backend, with no card networks and no money. That evidence is real, and it "
                    "does not pay for this: only a LIVE run can.)\n\n"
                    if test_only
                    else ""
                )
                + (
                    f"({', '.join(stale)} HAS been exercised in LIVE mode — but against code that "
                    "has CHANGED since. The gateway method was edited after the run, so the "
                    "evidence no longer describes what would execute, and a verification of code "
                    "nobody has run is not a verification. Re-run the harness for those "
                    "operations and record the new fingerprint.)\n\n"
                    if stale
                    else ""
                )
                + f"It must start with `{prefix}`. ==This is refused rather than warned about==: a "
                "live credential here would move real money through code exercised only with a "
                "stubbed transport, and every status code would say success.\n"
                "\n"
                "==The refusal is lifted by EVIDENCE, one operation at a time.== Exercise the "
                "operation against the real API (`apps/server/tests/live/`, zero-cost calls only) "
                "and record what came back as a `LiveVerification` in `live_verifications()`. The "
                "door opens when EVERY operation the gateway performs has one — a half-verified "
                "gateway is one whose other half moves somebody's money unseen. Meanwhile, use the "
                "test-mode credential from the provider's dashboard.\n"
                "\n"
                "(The value is not shown — it is a secret, wrong mode or not.)"
            )

    return {key: str(value) for key, value in present.items()}


async def _row_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: CredentialProvider
) -> TenantCredential | None:
    """The business's credential row, or ``None``.

    The ``tenant_id`` filter is belt AND braces: row-level security already makes another business's
    row invisible on the app role, and this clause keeps the query correct on the owner/worker roles
    too, which bypass RLS. Two independent reasons the wrong row cannot come back.
    """
    return (
        await session.scalars(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider.value,
            )
        )
    ).one_or_none()


# PLR0913: six, and every one of them is a fact this write cannot proceed without — the session, the
# business, the provider, the values, the key that encrypts them, and the evidence the live door
# reads. Collapsing a pair into a bag would hide the one argument that must not be forgettable.
async def store_credential(  # noqa: PLR0913
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    fernet_key: bytes,
    current_implementations: Mapping[GatewayOperation, str],
) -> TenantCredential:
    """Store (or REPLACE) a business's credential for ``provider``, encrypted. Flushes; no commit.

    Replacing rather than adding: one credential per provider per business, so "which of these two
    accounts do we charge into?" is a question this system never has to answer.

    ``current_implementations`` is the fingerprint of the gateway code that would run right now, one
    per operation — ``integrations.money.current_gateway_implementations(provider)``. ==It has no
    default, so this door cannot be opened without stating what the evidence would be about==; see
    :func:`verified_operations`. For an INFRA provider it is legitimately ``{}`` (there is no
    gateway), and it is still passed rather than assumed, because the caller that would have to
    "know" that is exactly the caller that gets it wrong for a money provider one day.
    """
    payload = json.dumps(
        _validate(provider, secrets, current_implementations=current_implementations),
        sort_keys=True,
    ).encode("utf-8")
    ciphertext = encrypt_secret(payload, fernet_key)

    existing = await _row_for(session, tenant_id=tenant_id, provider=provider)
    if existing is not None:
        existing.encrypted_payload = ciphertext
        await session.flush()
        return existing

    # The read above and this INSERT are not one act. A concurrent store_credential for the same
    # (tenant, provider) — two admin tabs, a retried request — can slip a row in between them, and
    # then the UNIQUE(tenant_id, provider) constraint refuses this one. On a payment credential "it
    # looked like it saved and then threw IntegrityError" is a failure the caller must never see. So
    # the INSERT runs inside a SAVEPOINT (the guarded pattern services/event_types.py uses for a
    # duplicate slug): the violation rolls back only this INSERT — not the caller's transaction —
    # and we re-read the row the racer just committed and UPDATE it, so the last writer wins and the
    # caller sees a clean re-save. Anything the re-read does NOT explain (the FOREIGN KEY refusing
    # an orphan tenant, say) is not ours to translate — it travels intact.
    credential = TenantCredential(
        tenant_id=tenant_id, provider=provider.value, encrypted_payload=ciphertext
    )
    try:
        async with session.begin_nested():
            session.add(credential)
            await session.flush()
    except IntegrityError:
        conflicting = await _row_for(session, tenant_id=tenant_id, provider=provider)
        if conflicting is None:
            raise
        conflicting.encrypted_payload = ciphertext
        await session.flush()
        return conflicting
    return credential


async def delete_credential(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: CredentialProvider
) -> bool:
    """Remove a business's credential. ==The OFF switch.== ``True`` if there was one to remove.

    For a money provider, off means **this business stops charging** — it does NOT mean "fall back
    to the instance's account". :func:`resolve_money_credential` raises from the next call onwards,
    which is the only safe reading of "the account is gone".
    """
    existing = await _row_for(session, tenant_id=tenant_id, provider=provider)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True


async def list_credential_providers(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> tuple[CredentialProvider, ...]:
    """Which providers this business has configured. ==Takes no key, so it can leak no secret.==

    "Is Stripe configured?" is answerable without decrypting anything, so it is answered without
    decrypting anything. The absent ``fernet_key`` parameter is the guarantee — not the intention.
    """
    rows = (
        await session.scalars(
            select(TenantCredential.provider)
            .where(TenantCredential.tenant_id == tenant_id)
            .order_by(TenantCredential.provider)
        )
    ).all()
    return tuple(CredentialProvider(value) for value in rows)


def _decrypt(
    row: TenantCredential, provider: CredentialProvider, key: bytes | Sequence[bytes]
) -> ResolvedCredential:
    payload = json.loads(decrypt_secret(row.encrypted_payload, key).decode("utf-8"))
    if not isinstance(payload, dict):
        # ==Valid JSON is not a valid credential.== A payload that decrypts to a number, a string,
        # an array or ``null`` has no fields, so ``.items()`` below would throw a bare
        # ``AttributeError`` — a raw crash on the money path, where a guest's card may already have
        # been charged. Raise a legible domain error instead. This is the READ mirror of the object
        # check the CLI does on WRITE; on the write path object-ness is otherwise guaranteed by
        # ``store_credential``'s ``Mapping[str, str]`` signature, so the runtime guard is only
        # needed here, where ``json.loads`` hands back ``Any``. The decrypted value is NEVER echoed
        # (it is a secret, malformed or not); only the provider is named.
        raise MalformedCredentialError(
            f"the stored {provider.value} credential did not decrypt to a JSON object of "
            "field → value, so it has no fields to read. It is corrupt and cannot be used; "
            "re-enter it with `aethercal-admin credentials set`. (The decrypted value is not "
            "shown — it is a secret, malformed or not.)"
        )
    return ResolvedCredential(
        provider=provider,
        source=CredentialSource.TENANT,
        secrets={str(field): str(value) for field, value in payload.items()},
    )


async def resolve_tenant_money_provider(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> CredentialProvider:
    """Which provider this business charges with. ==DERIVED from its credential, never defaulted.==

    The rule is the whole of the routing decision, and it is three branches long because the
    alternative to each is worse:

    * **none** → :class:`MissingCredentialError`. The pre-existing refusal, unchanged and re-raised
      with the same type so :func:`resolve_money_credential`'s callers (and the public API's 402)
      keep behaving exactly as they did;
    * **exactly one** → that one. The real case. ==The credential IS the decision==, which is why
      this needs no flag, no column and no default: a business that configured Mercado Pago has
      already said what it wants, and asking it to say so twice is how the two answers drift apart;
    * **two** → :class:`AmbiguousMoneyProviderError`. ==Never a silent pick.== See that class for
      why, and for the real fix if the ambiguity ever bites.

    ==Takes no ``fernet_key``, so it can leak no secret.== "Which provider?" is answerable without
    decrypting anything, so it is answered without decrypting anything — the absent parameter is the
    guarantee, exactly as in :func:`list_credential_providers`, which does the reading.
    """
    configured = await list_credential_providers(session, tenant_id=tenant_id)
    money = [
        provider for provider in configured if credential_class(provider) is CredentialClass.MONEY
    ]
    if not money:
        raise MissingCredentialError(
            f"business {tenant_id} has no payment credential of its own, so it cannot charge.\n"
            "\n"
            "==This does NOT fall back to the instance's account.== Configure the business's own "
            "credential (`aethercal-admin credentials set --provider stripe|mercado_pago`), or "
            "leave the event type free of charge."
        )
    if len(money) > 1:
        names = ", ".join(sorted(provider.value for provider in money))
        raise AmbiguousMoneyProviderError(
            f"business {tenant_id} has more than one payment credential ({names}), and nothing "
            "says which account its guests should pay into.\n"
            "\n"
            "==This is refused rather than guessed.== Picking one would put a guest's money in an "
            "account the business may not have meant, and it would not look like a failure — every "
            "status code would say success.\n"
            "\n"
            "Remove the credential this business does not charge with "
            "(`aethercal-admin credentials delete --provider <name>`). If a business genuinely "
            "needs both configured at once, that needs a per-tenant preference field — a product "
            "decision with a migration, not a default."
        )
    return money[0]


async def resolve_money_credential(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    fernet_key: bytes | Sequence[bytes],
) -> ResolvedCredential:
    """The business's OWN payment credential. ==No fallback exists here.== Criteria 40 and 41.

    ==There is no ``instance_default`` parameter, and that is the design.== A caller cannot pass one
    in a hurry, a reviewer does not have to catch that they did, and putting one back is an edit to
    this signature — which a test asserts against, by name.

    ``fernet_key`` is one key normally, and the ``(current, previous)`` reader during a key rotation
    (``Settings.decryption_fernet_keys()``): a credential the rotation has not reached yet — still
    on the retiring key — must stay chargeable throughout the window, or a guest cannot pay.

    Raises :class:`MissingCredentialError` when the business has no credential of its own: it does
    not charge. Raises :class:`WrongCredentialClassError` if handed an INFRA provider — the two
    doors are not interchangeable.
    """
    if credential_class(provider) is not CredentialClass.MONEY:
        raise WrongCredentialClassError(
            f"{provider.value} is not a money provider, so it does not belong on this path. Use "
            "resolve_infra_credential, which may fall back to the instance's own configuration — a "
            "fallback that must never be reachable for a provider that moves somebody else's money."
        )

    row = await _row_for(session, tenant_id=tenant_id, provider=provider)
    if row is None:
        raise MissingCredentialError(
            f"business {tenant_id} has no {provider.value} credential of its own, so it cannot "
            "charge.\n"
            "\n"
            "==This does NOT fall back to the instance's account.== Falling back would mean this "
            "business's guest paying into the INSTANCE OPERATOR's payment account — which is not a "
            "degraded mode. It is charging with somebody else's account.\n"
            "\n"
            "Configure the business's own credential (`aethercal-admin credentials set --provider "
            f"{provider.value}`), or leave the event type free of charge."
        )
    return _decrypt(row, provider, fernet_key)


async def resolve_infra_credential(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    fernet_key: bytes | Sequence[bytes],
    instance_default: Mapping[str, str] | None,
) -> ResolvedCredential | None:
    """The business's own SENDING credential if it has one, else the instance's. ==Precedence.==

    Returns ``None`` when there is neither — which is what "this channel is switched off" has always
    meant here: the channel is absent from the drain's registry and its steps skip with a reason. An
    unconfigured WhatsApp must not 500 a booking.

    Raises :class:`WrongCredentialClassError` when handed a MONEY provider. ==That refusal is what
    keeps criterion 41 from being one careless call away from a bypass==: this is the only function
    in the product that can return the INSTANCE's own credentials, so it is the one place a payment
    provider must never be allowed to arrive.

    .. rubric:: ==Whether an instance default EXISTS is not decided here (B-03bis)==

    ``instance_default`` is a parameter, and this function trusts it. That is deliberate: whether
    the operator's configuration may stand in for a business at all depends on what kind of thing
    that configuration IS, and this module cannot know. An SMTP relay is a pipe (the ``From``
    travels per message, so a business's mail goes through it AS the business); a WhatsApp number is
    an identity (there is no per-message ``From``, so lending it sends the message AS THE OPERATOR).

    :func:`~aethercal.server.services.tenant_senders.instance_fallback` makes that call, per
    provider and exhaustively, and passes ``None`` here for a provider whose default must not be
    reachable. So the precedence lives in one place — this one — and the question of what may be
    offered to it lives in the module that knows.
    """
    if credential_class(provider) is not CredentialClass.INFRA:
        raise WrongCredentialClassError(
            f"{provider.value} handles money, and this is the only door with an instance-default "
            "fallback behind it. Sending it through here would let a business charge into the "
            "INSTANCE OPERATOR's account whenever it had no credential of its own — the exact "
            "failure the money path is fail-closed to prevent. Use resolve_money_credential."
        )

    row = await _row_for(session, tenant_id=tenant_id, provider=provider)
    if row is not None:
        return _decrypt(row, provider, fernet_key)  # ==the business's own wins==
    if instance_default is None:
        return None  # the channel is simply off — a decision, not a failure
    return ResolvedCredential(
        provider=provider,
        source=CredentialSource.INSTANCE,
        secrets={str(field): str(value) for field, value in instance_default.items()},
    )


__all__ = [
    "KEY_BODY_CHARACTERS",
    "AmbiguousMoneyProviderError",
    "CredentialClass",
    "CredentialError",
    "CredentialProvider",
    "CredentialSource",
    "GatewayOperation",
    "IncompleteCredentialError",
    "KeyFamily",
    "LiveCredentialRefusedError",
    "LiveVerification",
    "MalformedCredentialError",
    "MissingCredentialError",
    "MoneyDirection",
    "ProviderMode",
    "ResolvedCredential",
    "StaleVerificationError",
    "UnrecognisedCredentialError",
    "WrongCredentialClassError",
    "authorise_live_use",
    "authorises_live_credentials",
    "blocks_on_stale_evidence",
    "credential_class",
    "credential_key_families",
    "declared_test_mode_prefixes",
    "delete_credential",
    "gateway_operations",
    "is_live_credential",
    "list_credential_providers",
    "live_verifications",
    "money_direction",
    "required_fields",
    "required_test_mode_prefixes",
    "resolve_infra_credential",
    "resolve_money_credential",
    "resolve_tenant_money_provider",
    "stale_verifications",
    "store_credential",
    "unverified_operations",
    "verified_operations",
]
