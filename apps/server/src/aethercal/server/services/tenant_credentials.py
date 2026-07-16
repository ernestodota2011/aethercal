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
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
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


def _validate(provider: CredentialProvider, secrets: Mapping[str, str]) -> dict[str, str]:
    """Refuse a half-configured credential AT THE DOOR, naming what is missing."""
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


async def store_credential(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    fernet_key: bytes,
) -> TenantCredential:
    """Store (or REPLACE) a business's credential for ``provider``, encrypted. Flushes; no commit.

    Replacing rather than adding: one credential per provider per business, so "which of these two
    accounts do we charge into?" is a question this system never has to answer.
    """
    payload = json.dumps(_validate(provider, secrets), sort_keys=True).encode("utf-8")
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


def _decrypt(row: TenantCredential, provider: CredentialProvider, key: bytes) -> ResolvedCredential:
    payload = json.loads(decrypt_secret(row.encrypted_payload, key).decode("utf-8"))
    return ResolvedCredential(
        provider=provider,
        source=CredentialSource.TENANT,
        secrets={str(field): str(value) for field, value in payload.items()},
    )


async def resolve_money_credential(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    fernet_key: bytes,
) -> ResolvedCredential:
    """The business's OWN payment credential. ==No fallback exists here.== Criteria 40 and 41.

    ==There is no ``instance_default`` parameter, and that is the design.== A caller cannot pass one
    in a hurry, a reviewer does not have to catch that they did, and putting one back is an edit to
    this signature — which a test asserts against, by name.

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
    fernet_key: bytes,
    instance_default: Mapping[str, str] | None,
) -> ResolvedCredential | None:
    """The business's own SENDING credential if it has one, else the instance's. ==Precedence.==

    Returns ``None`` when there is neither — which is what "this channel is switched off" has always
    meant here (``build_channel_senders``: the channel is absent from the registry and its steps
    skip with a reason). An unconfigured WhatsApp must not 500 a booking.

    Raises :class:`WrongCredentialClassError` when handed a MONEY provider. ==That refusal is what
    keeps criterion 41 from being one careless call away from a bypass==: this is the only function
    in the product that can return the INSTANCE's own credentials, so it is the one place a payment
    provider must never be allowed to arrive.
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
    "CredentialClass",
    "CredentialError",
    "CredentialProvider",
    "CredentialSource",
    "IncompleteCredentialError",
    "MissingCredentialError",
    "ResolvedCredential",
    "WrongCredentialClassError",
    "credential_class",
    "delete_credential",
    "list_credential_providers",
    "required_fields",
    "resolve_infra_credential",
    "resolve_money_credential",
    "store_credential",
]
