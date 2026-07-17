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

from typing import assert_never

from aethercal.server.integrations.mercadopago import MercadoPagoGateway, MercadoPagoWebhookAdapter
from aethercal.server.integrations.stripe import StripeGateway, StripeWebhookAdapter
from aethercal.server.services.payment_webhooks import PaymentWebhookAdapter
from aethercal.server.services.payments import PaymentGateway
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    credential_class,
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


__all__ = [
    "NotAMoneyProviderError",
    "build_payment_gateways",
    "build_webhook_adapters",
    "gateway_for",
    "money_providers",
    "webhook_adapter_for",
]
