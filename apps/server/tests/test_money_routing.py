"""Which provider a business charges with — ==DERIVED from its credential, never defaulted== (B-06).

The rule, and the whole of it:

* **no money credential** → it does not charge. That was already true and is not re-litigated here;
  :func:`resolve_money_credential` raises and the public API answers 402;
* **exactly one** → that one. The real case;
* **two** → an explicit, fail-closed refusal that NAMES the ambiguity. ==Not a silent pick.==
  Without a per-tenant preference field there is no fact that says which account the business wants
  its guests' money in, so choosing one would be a guess — and a guess here charges into the wrong
  account and looks like it worked.

The debt this leaves is deliberate and is written down: if the ambiguity turns out to be a nuisance
in practice, the fix is a **per-tenant preference field** (a migration plus admin UI — a product
decision), not a default smuggled in behind the first person it inconveniences.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.integrations.mercadopago import MercadoPagoGateway
from aethercal.server.integrations.money import build_payment_gateways
from aethercal.server.integrations.stripe import StripeGateway
from aethercal.server.services.payments import (
    CHECKOUT_SESSION_TTL,
    HOLD_TTL,
    min_hold_remaining_for_checkout,
)
from aethercal.server.services.tenant_credentials import (
    AmbiguousMoneyProviderError,
    CredentialProvider,
    MissingCredentialError,
    resolve_tenant_money_provider,
    store_credential,
)

_KEY = derive_fernet_key("test-app-secret-not-a-real-one")

_SECRETS: dict[CredentialProvider, dict[str, str]] = {
    CredentialProvider.STRIPE: {"secret_key": "sk_test_x", "webhook_secret": "whsec_x"},
    CredentialProvider.MERCADO_PAGO: {"access_token": "TEST-x", "webhook_secret": "mp_x"},
    CredentialProvider.SMTP: {"host": "smtp.example.com", "from_addr": "a@example.com"},
}


async def _tenant(session: AsyncSession, *providers: CredentialProvider) -> uuid.UUID:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    for provider in providers:
        await store_credential(
            session,
            tenant_id=tenant.id,
            provider=provider,
            secrets=_SECRETS[provider],
            fernet_key=_KEY,
        )
    return tenant.id


async def test_a_business_with_one_money_credential_charges_with_that_provider(
    sqlite_session: AsyncSession,
) -> None:
    """The 99% case: the credential IS the decision. No flag, no default, no lookup table."""
    tenant_id = await _tenant(sqlite_session, CredentialProvider.STRIPE)
    resolved = await resolve_tenant_money_provider(sqlite_session, tenant_id=tenant_id)
    assert resolved is CredentialProvider.STRIPE


async def test_a_mercado_pago_business_charges_with_mercado_pago(
    sqlite_session: AsyncSession,
) -> None:
    """==The reason B-06 exists.== Before the routing, this business could not be charged at all:
    the checkout path asked Stripe regardless of what the business had configured."""
    tenant_id = await _tenant(sqlite_session, CredentialProvider.MERCADO_PAGO)
    resolved = await resolve_tenant_money_provider(sqlite_session, tenant_id=tenant_id)
    assert resolved is CredentialProvider.MERCADO_PAGO


async def test_a_sending_credential_does_not_make_a_business_chargeable(
    sqlite_session: AsyncSession,
) -> None:
    """SMTP is not money. A business with a mail relay and no payment account does not charge."""
    tenant_id = await _tenant(sqlite_session, CredentialProvider.SMTP)
    with pytest.raises(MissingCredentialError):
        await resolve_tenant_money_provider(sqlite_session, tenant_id=tenant_id)


async def test_a_business_with_no_credential_at_all_does_not_charge(
    sqlite_session: AsyncSession,
) -> None:
    """==Unchanged, and deliberately so.== The pre-existing fail-closed refusal is the right one;
    the routing raises the SAME error type so the public API's 402 mapping still holds."""
    tenant_id = await _tenant(sqlite_session)
    with pytest.raises(MissingCredentialError):
        await resolve_tenant_money_provider(sqlite_session, tenant_id=tenant_id)


async def test_two_money_credentials_are_an_explicit_refusal_that_names_the_ambiguity(
    sqlite_session: AsyncSession,
) -> None:
    """==Fail-closed on ambiguity. The one thing this must never do is pick one.==

    With both configured there is no fact in the system that says which account the business wants
    the money in — so a choice here would be a guess, and a guess that charges into the wrong
    account does not look like a failure. It looks like it worked. The error names both providers so
    whoever reads it knows what to remove.
    """
    tenant_id = await _tenant(
        sqlite_session, CredentialProvider.STRIPE, CredentialProvider.MERCADO_PAGO
    )
    with pytest.raises(AmbiguousMoneyProviderError) as excinfo:
        await resolve_tenant_money_provider(sqlite_session, tenant_id=tenant_id)
    message = str(excinfo.value)
    assert "stripe" in message and "mercado_pago" in message


async def test_the_ambiguous_refusal_is_not_a_missing_credential(
    sqlite_session: AsyncSession,
) -> None:
    """The two failures are different facts and must stay tellable apart: "you configured none" is
    the business's setup step, "you configured two" is a misconfiguration a human must resolve. A
    subclass relationship here would let a caller catch one and silently absorb the other."""
    assert not issubclass(AmbiguousMoneyProviderError, MissingCredentialError)
    assert not issubclass(MissingCredentialError, AmbiguousMoneyProviderError)


def test_the_gateway_map_is_built_for_every_money_provider() -> None:
    """The map the checkout and the refund runner look up, keyed by the stored provider value."""
    gateways = build_payment_gateways()
    assert isinstance(gateways["stripe"], StripeGateway)
    assert isinstance(gateways["mercado_pago"], MercadoPagoGateway)


# --------------------------------------------------------------------------------------
# ==The checkout floor belongs to the PROVIDER, not to the arbiter== (B-06).
# --------------------------------------------------------------------------------------


def test_stripe_declares_its_own_thirty_minute_floor() -> None:
    """Stripe rejects a Checkout Session expiring under 30 minutes out. The number lived in
    ``services/payments`` as a constant documented as "Stripe's floor" — inside the provider-
    AGNOSTIC arbiter. It now lives with the provider that has the rule."""
    assert StripeGateway().checkout_session_floor == timedelta(minutes=30)


def test_mercado_pago_declares_no_floor_at_all() -> None:
    """Mercado Pago documents ``expires``/``expiration_date_to`` with no minimum and no maximum."""
    assert MercadoPagoGateway().checkout_session_floor == timedelta(0)


def test_the_stripe_resume_threshold_is_unchanged_by_the_refactor() -> None:
    """==A regression guard on a REFACTOR, and the point of writing it.==

    The threshold used to be ``CHECKOUT_SESSION_TTL`` (31 min) for everyone. For Stripe the new
    derivation — its own floor plus the latency buffer — must land on exactly the same number, or
    this "move the constant" quietly changed Stripe's behaviour while claiming not to.
    """
    assert min_hold_remaining_for_checkout(StripeGateway()) == CHECKOUT_SESSION_TTL
    assert min_hold_remaining_for_checkout(StripeGateway()) == timedelta(minutes=31)


def test_mercado_pago_holds_stay_resumable_for_nearly_their_whole_life() -> None:
    """==The improvement, asserted so it is on the record.==

    Against a 33-minute hold, Stripe's 31-minute threshold leaves a resume window roughly TWO
    minutes wide — and that window was never a product decision, it was Stripe's floor showing
    through the arbiter. Mercado Pago has no floor, so its threshold is the latency buffer alone and
    its window is ~32 minutes. The constraint now follows the provider that imposes it.
    """
    stripe_window = HOLD_TTL - min_hold_remaining_for_checkout(StripeGateway())
    mercado_pago_window = HOLD_TTL - min_hold_remaining_for_checkout(MercadoPagoGateway())

    assert stripe_window == timedelta(minutes=2)
    assert mercado_pago_window == timedelta(minutes=32)
    assert mercado_pago_window > stripe_window
