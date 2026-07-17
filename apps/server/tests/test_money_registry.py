"""The money registry's lock: ==every MONEY provider has a real adapter and a real gateway== (B-06).

The registry this guards replaced a hand-written dict that listed ``mercado_pago`` and handed it a
generic HMAC adapter which could never have verified a real Mercado Pago notification. The point of
these tests is that the same class of lie cannot be told again.

==The provider set is DERIVED, never typed out here.== Every test below re-computes the money
providers from :func:`~aethercal.server.services.tenant_credentials.credential_class` — the same
function the BYOK fallback rule reads off. A new payment processor added to ``CredentialProvider``
lands in these tests automatically and fails them until it has an adapter and a gateway. A test that
listed the providers by hand would just be a second copy of the photograph, going stale beside the
first.
"""

from __future__ import annotations

import pytest

from aethercal.server.integrations.money import (
    NotAMoneyProviderError,
    build_webhook_adapters,
    gateway_for,
    money_providers,
    webhook_adapter_for,
)
from aethercal.server.services.payment_webhooks import GenericHmacAdapter
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    credential_class,
)


def test_the_money_providers_are_derived_from_the_credential_class() -> None:
    """The registry and the BYOK fallback rule read the same source, by construction."""
    assert set(money_providers()) == {
        provider
        for provider in CredentialProvider
        if credential_class(provider) is CredentialClass.MONEY
    }
    # If this ever empties, every assertion below passes vacuously — so it is pinned.
    assert money_providers(), "a product that charges must have at least one money provider"


@pytest.mark.parametrize("provider", money_providers())
def test_every_money_provider_has_a_webhook_adapter(provider: CredentialProvider) -> None:
    """==The exhaustiveness lock.== A money provider without an adapter cannot be shipped: its
    webhooks would have no way to be verified, so a guest could pay and never be confirmed."""
    adapter = webhook_adapter_for(provider)
    assert adapter is not None
    assert hasattr(adapter, "verify_signature")
    assert hasattr(adapter, "parse")


@pytest.mark.parametrize("provider", money_providers())
def test_no_money_provider_is_served_by_the_generic_fake(provider: CredentialProvider) -> None:
    """==The regression that names the original defect.==

    ``GenericHmacAdapter`` is the test fake. It once sat in the registry under ``mercado_pago``,
    which advertised support for a provider whose real signature scheme (an ``x-signature`` manifest
    over ``data.id``/``x-request-id``/``ts``) it does not implement and whose notifications it would
    therefore have rejected outright. A fake standing in for a provider is not support; it is a
    registry entry that looks like support.
    """
    assert not isinstance(webhook_adapter_for(provider), GenericHmacAdapter)


@pytest.mark.parametrize("provider", money_providers())
def test_every_money_provider_has_a_gateway(provider: CredentialProvider) -> None:
    """A provider that can receive webhooks but cannot open a checkout or refund is half a
    provider — and the half that is missing is the one that gives a guest's money back."""
    gateway = gateway_for(provider)
    assert gateway is not None
    assert hasattr(gateway, "create_checkout_session")
    assert hasattr(gateway, "refund")


@pytest.mark.parametrize(
    "provider",
    [p for p in CredentialProvider if credential_class(p) is CredentialClass.INFRA],
)
def test_an_infra_provider_has_no_payment_adapter_or_gateway(provider: CredentialProvider) -> None:
    """==Raised, not returned as ``None``.== Asking for SMTP's payment gateway is a category error,
    and a ``None`` on the money path is read by the first hurried caller as "not configured, carry
    on" — the sentence the whole BYOK module exists to make unsayable."""
    with pytest.raises(NotAMoneyProviderError):
        webhook_adapter_for(provider)
    with pytest.raises(NotAMoneyProviderError):
        gateway_for(provider)


def test_the_router_map_is_keyed_by_the_stored_provider_value() -> None:
    """The ``POST /webhooks/{provider}/{tenant_slug}`` route carries the credential provider's
    stored string, so the map the router looks up must be keyed by exactly that — and by every money
    provider, so none is unreachable."""
    adapters = build_webhook_adapters()
    assert set(adapters) == {provider.value for provider in money_providers()}
    assert "mercado_pago" in adapters, "the route the docs publish must resolve to an adapter"
