"""Which adapter and which gateway a money provider gets. ==Exhaustive, so none can be forgotten.==

This module exists because the thing it replaces was a hand-written dict:

.. code-block:: python

    PAYMENT_WEBHOOK_ADAPTERS: dict[str, PaymentWebhookAdapter] = {
        "stripe": GenericHmacAdapter(),
        "mercado_pago": GenericHmacAdapter(),   # <- this line was a lie
    }

==That table said Mercado Pago was supported, and it was not.== ``GenericHmacAdapter`` checks an
``X-Webhook-Signature`` header that Mercado Pago has never sent, so every real Mercado Pago
notification would have failed verification and 401'd. It failed CLOSED, which is the only reason it
was not a disaster — but a registry whose entries are written by hand is a photograph of what
somebody believed on the day they typed it, and nothing keeps it true afterwards. A provider added
to :class:`~aethercal.server.services.tenant_credentials.CredentialProvider` would simply be missing
here, and ``adapters.get(provider)`` would answer ``None``.

So the mapping is a ``match`` with :func:`typing.assert_never`, over the SAME enum
``credential_class`` is exhaustive over. ==Adding a money provider without deciding its adapter and
its gateway does not type-check==, and ``tests/test_money_registry.py`` re-derives the money
providers from :func:`~aethercal.server.services.tenant_credentials.credential_class` — not from a
list in the test — so the lock cannot be satisfied by editing a fixture.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping
from typing import assert_never

from aethercal.server.integrations.mercadopago import MercadoPagoGateway, MercadoPagoWebhookAdapter
from aethercal.server.integrations.stripe import StripeGateway, StripeWebhookAdapter
from aethercal.server.services.payment_webhooks import PaymentWebhookAdapter
from aethercal.server.services.payments import PaymentGateway
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    GatewayOperation,
    credential_class,
    gateway_operations,
)


class NotAMoneyProviderError(RuntimeError):
    """An INFRA provider was asked for a payment adapter or gateway.

    Not a lookup miss — a category error, and it is raised rather than returned as ``None`` for the
    reason :class:`~aethercal.server.services.tenant_credentials.MissingCredentialError` is: a
    ``None`` on the money path is read by the first hurried caller as "nothing configured, carry
    on" — the sentence the BYOK module exists to make unsayable.
    """


def webhook_adapter_for(provider: CredentialProvider) -> PaymentWebhookAdapter:
    """The inbound-webhook adapter for one money provider. ==Exhaustive; no default branch.==

    A new payment processor cannot inherit some other provider's signature scheme by omission — the
    ``assert_never`` refuses to type-check until its adapter is named here.
    """
    match provider:
        case CredentialProvider.STRIPE:
            return StripeWebhookAdapter()
        case CredentialProvider.MERCADO_PAGO:
            return MercadoPagoWebhookAdapter()
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            raise NotAMoneyProviderError(_not_money(provider))
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def gateway_for(provider: CredentialProvider) -> PaymentGateway:
    """The outgoing gateway for one money provider. ==Exhaustive, for the same reason.==

    WHICH provider a business charges with is decided by
    :func:`~aethercal.server.services.tenant_credentials.resolve_tenant_money_provider` — derived
    from the money credential the business configured, never defaulted. This turns that answer into
    the object that can actually talk to it.
    """
    match provider:
        case CredentialProvider.STRIPE:
            return StripeGateway()
        case CredentialProvider.MERCADO_PAGO:
            return MercadoPagoGateway()
        case CredentialProvider.SMTP | CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            raise NotAMoneyProviderError(_not_money(provider))
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def gateway_method_for(operation: GatewayOperation) -> str:
    """Which gateway call each domain operation names. ==Exhaustive, and it lives HERE.==

    ``GatewayOperation`` is in ``services`` because the credential door reads it, and the gateways
    are in ``integrations``; this module is the one place that legitimately knows both. A third
    operation (F5's partial refund, a capture) does not type-check until somebody has said which
    call it stands for.
    """
    match operation:
        case GatewayOperation.CHECKOUT:
            return "create_checkout_session"
        case GatewayOperation.REFUND:
            return "refund"
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def read_only_gateway_methods() -> frozenset[str]:
    """Gateway calls that move NO money. ==Declared, so a new one cannot arrive unclassified.==

    :class:`~aethercal.server.services.tenant_credentials.GatewayOperation` names the acts that move
    money, and every one of them must be VERIFIED against the real provider before a live credential
    may be stored. A read is not one of those: ``refund_status`` asks the provider a question, and
    its worst failure is a refund that settles late — the recoverable direction, and not one that
    can move a guest's money through unexercised code.

    It is DECLARED rather than inferred so the anti-omission lock keeps working: the suite asserts
    that the protocol's coroutines are EXACTLY the money operations plus these, and that the two
    sets are disjoint — so a new method on the gateway is not classified as either until somebody
    has said which it is.
    """
    return frozenset({"refund_status"})


def implementation_fingerprint(provider: CredentialProvider, operation: GatewayOperation) -> str:
    """A reproducible identity of the CODE that performs ``operation`` for ``provider``.

    .. rubric:: ==Why a verification has to name the code it exercised==

    ``live_verifications`` records that somebody ran the adapter against the real API on a given
    day. It did NOT record *which adapter* — so the day after somebody rewrites
    ``StripeGateway.refund``, the register still says "verified", about code ==no human being has
    ever run==. The evidence would go on authorising a live credential for an implementation that
    did not exist when the evidence was gathered. That is ``feedback_justificacion_caduca`` exactly:
    a justification about a moving target, written once.

    So each record carries this fingerprint, and ==the credential door re-computes it on every
    write== (:func:`current_gateway_implementations`, handed to
    :func:`~aethercal.server.services.tenant_credentials.verified_operations`). Editing the method
    ==invalidates its verification== and demands a fresh run, which is the only honest outcome.

    .. rubric:: ==What it hashes: the gateway's whole MODULE, not the method==

    It hashed ``inspect.getsource(method)`` — the method's own text — and that was the same defect
    one level down. ``StripeGateway.refund`` is four lines that delegate: the request is actually
    built by ``self._client``, out of the module constants ``_STRIPE_API_BASE`` (the URL this
    supposedly spoke to) and ``_HTTP_TIMEOUT``. ==Repoint the base URL and the method's own text
    does not move a byte==, so a verification reading "exercised against the real API" would go on
    authorising a live credential for code that now talks somewhere else entirely. Mercado Pago's
    gateway is built the same way, out of ``_MP_API_BASE``.

    So the unit is the module that defines the gateway. Any edit to it — the method, the client
    builder, a constant, a comment, a reformat — changes the hash.

    .. rubric:: Why blunt rather than clever

    The precise answer is the transitive closure of what the method reaches, computed by walking
    the AST. It was rejected: ==an incomplete analysis fails in the EXPENSIVE direction== — it
    answers "unchanged" for an edit it did not follow, which is a false "still verified", which is
    somebody's money moving through code nobody ran. A hash of the file cannot be incomplete about
    the file.
    The cost is over-invalidation, and this module already chose that asymmetry in so many words.

    That cost is smaller than it looks, too: the fingerprint is read on the credential WRITE path
    only, so a stale one blocks storing a NEW live credential — it never disables one already
    stored, and it stops nobody from trading. Nobody is under pressure to delete this guard to ship.

    .. rubric:: ==What it still does NOT cover — said plainly, because a hash looks total==

    * **anything in another module.** ``CheckoutSession`` (``services.payments``) and the webhook
      types are named here and hashed nowhere; a change to them does not move this;
    * **third-party code.** ``httpx`` — its version, TLS defaults, proxy handling — is the layer
      that actually puts bytes on the wire, and a dependency bump leaves this identical;
    * **the injected ``transport``.** Production passes ``None``; a caller injecting one runs a
      different transport under the same fingerprint;
    * **the runtime environment.** Proxies, CA bundles, ``*_PROXY`` variables.

    Covering the first two means hashing a resolved dependency set into every record — a different
    design with a real price, since every ``uv.lock`` bump would then invalidate ``refund``, and
    re-verifying ``refund`` costs a REAL $1 charge somebody has to pay. Deliberately not done here,
    and it sustains no false claim today: the register is empty, so there is no verification for the
    gap to keep alive. Worth revisiting the day one exists.

    .. rubric:: Both operations of a provider share a fingerprint, on purpose

    They come from one module, so an edit to either invalidates both. The alternative — a hash able
    to say "checkout is still fine" about a file that changed underneath it — is the claim this
    function exists to stop making. If that coupling ever forces a real re-charge for an unrelated
    edit, the fix is structural (give the gateway a module of its own, away from the webhook
    adapter), not a cleverer hash.
    """
    gateway = gateway_for(provider)  # raises NotAMoneyProviderError for an INFRA provider
    # Resolved and then discarded: the operation must name a method the gateway really has. An
    # AttributeError here is a louder, earlier failure than a fingerprint of a module whose gateway
    # cannot perform the operation the record claims.
    getattr(type(gateway), gateway_method_for(operation))
    return module_fingerprint(type(gateway))


def module_fingerprint(obj: object) -> str:
    """Hash the SOURCE of the module that defines ``obj``. ==The unit a verification is about.==

    Split out from :func:`implementation_fingerprint` so the suite can run it against a deliberately
    edited copy of a gateway module and prove the fingerprint moves — the sabotage that gives this
    check its meaning. Exercising the real hashing on a doctored input beats re-deriving the hash in
    a test, which would only prove the test agrees with itself.
    """
    module = inspect.getmodule(obj)
    if module is None:  # pragma: no cover - a gateway always lives in a real, importable module
        raise NotAMoneyProviderError(
            f"cannot locate the module that defines {obj!r}, so there is nothing to fingerprint "
            "and no verification about it could ever be checked for staleness"
        )
    return hashlib.sha256(inspect.getsource(module).encode("utf-8")).hexdigest()[:16]


def current_gateway_implementations(
    provider: CredentialProvider,
) -> Mapping[GatewayOperation, str]:
    """The fingerprint of the code that would run RIGHT NOW, per operation. ==The door's input.==

    .. rubric:: ==This function exists because the check could not live where the decision does==

    ``services.tenant_credentials`` owns the decision *may a live credential be stored?*, and it
    cannot compute a fingerprint: this module imports it, so the reverse edge is a cycle. The first
    cut resolved that by moving the comparison OUT of the decision and into
    ``tests/test_credential_mode_guard.py`` — which meant a rewritten ``StripeGateway.refund``
    invalidated its verification **in CI only**, while the production door went on authorising a
    live key against an implementation nobody had ever exercised. ==The evidence expired in the
    suite and stayed valid in production.==

    So the dependency is INVERTED rather than dropped: the layer that can see both sides computes
    the fact and hands it to the decision
    (:func:`~aethercal.server.services.tenant_credentials.verified_operations`, whose parameter has
    no default). ``cli.run_credentials_set`` — the operator's actual path to storing a credential —
    calls this.

    Derived from :func:`~aethercal.server.services.tenant_credentials.gateway_operations`, so a
    THIRD operation is fingerprinted the day it is added rather than the day somebody remembers.
    An INFRA provider has no gateway operations, so the answer is ``{}`` and
    :func:`implementation_fingerprint` (which refuses one) is never reached.
    """
    return {
        operation: implementation_fingerprint(provider, operation)
        for operation in gateway_operations(provider)
    }


def _not_money(provider: CredentialProvider) -> str:
    return (
        f"{provider.value} does not move money, so it has no payment adapter or gateway. "
        f"Its credential class is {credential_class(provider).value}."
    )


def money_providers() -> tuple[CredentialProvider, ...]:
    """Every provider that moves money. ==DERIVED from ``credential_class``, never enumerated.==

    The one place the set of money providers is computed, so the registry's exhaustiveness test and
    any future routing agree with :mod:`~aethercal.server.services.tenant_credentials` by
    construction rather than by somebody remembering to update both.
    """
    return tuple(
        provider
        for provider in CredentialProvider
        if credential_class(provider) is CredentialClass.MONEY
    )


def build_webhook_adapters() -> dict[str, PaymentWebhookAdapter]:
    """The router's provider→adapter map, built from :func:`money_providers`.

    Keyed by the credential provider's stored string, which is exactly what the
    ``POST /webhooks/{provider}/{tenant_slug}`` route carries — so the route selects the adapter and
    nothing has to be kept in step by hand.
    """
    return {provider.value: webhook_adapter_for(provider) for provider in money_providers()}


def build_payment_gateways() -> dict[str, PaymentGateway]:
    """The provider→gateway map the checkout and the REFUND runner look up.

    Keyed by the stored provider value — the same string ``payments.provider`` holds and the refund
    intent's payload carries — so the runner can route a refund to the gateway that can actually
    perform it. ==That routing is the fix for a real defect==: with one instance-wide
    ``StripeGateway``, a Mercado Pago refund resolved the business's Mercado Pago credential and
    handed it to Stripe's gateway, which read ``secrets["secret_key"]``, raised ``KeyError`` and
    retried for ever. Built from :func:`money_providers`, so a provider cannot be left unroutable.
    """
    return {provider.value: gateway_for(provider) for provider in money_providers()}


def build_gateway_implementations() -> dict[str, Mapping[GatewayOperation, str]]:
    """The provider→fingerprints map the USE gate reads. ==The twin of the gateway map.==

    Built and stored beside :func:`build_payment_gateways`, keyed by the same stored provider
    string, because the two answer halves of one question: *which object performs this act*, and
    *has THAT code been exercised*. Keeping them apart on the shelf is how one gets wired and the
    other forgotten.

    Computed once at boot rather than per request: it is a pure function of the source text of this
    process, so it cannot change while the process lives — and re-hashing a module on every checkout
    would put file I/O on the money path for an answer that is constant.
    """
    return {
        provider.value: current_gateway_implementations(provider) for provider in money_providers()
    }


__all__ = [
    "NotAMoneyProviderError",
    "build_gateway_implementations",
    "build_payment_gateways",
    "build_webhook_adapters",
    "current_gateway_implementations",
    "gateway_for",
    "gateway_method_for",
    "implementation_fingerprint",
    "module_fingerprint",
    "money_providers",
    "read_only_gateway_methods",
    "webhook_adapter_for",
]
