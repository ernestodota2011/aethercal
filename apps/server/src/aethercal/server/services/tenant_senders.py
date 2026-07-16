"""==THE SENDING FUNNEL: a live sender cannot be built without naming a business (B-03bis).==

B-03 built the BYOK machinery — one encrypted credential per (business, provider), and the two doors
that read it. It did not connect the SENDERS to it, and that gap is what this module closes.

.. rubric:: What was actually wrong

``app.build_email_sender`` / ``app.build_channel_senders`` read the instance's environment ONCE, at
boot, and put the resulting objects on ``app.state``. The drain then took those objects — bound
before any business was known — and pushed EVERY business's message through them
(``scheduler.make_outbox_drain_tick`` → ``make_booking_effect_executor(sender=…, channels=…)``).
The process was multi-tenant; the sender was a singleton. ==So a business's WhatsApp reminder went
out from the INSTANCE OPERATOR's number==, and its guest replied to a stranger.

That is not a missing feature. It breaks the two rules this product is sold on: a business brings
its OWN credentials, and one business's act never leaves the instance wearing another party's
identity.

.. rubric:: The fix is the SHAPE, not a check

:func:`resolve_tenant_senders` **takes a ``tenant_id`` and has no default for it**. Every live
sending client in the product is constructed inside this module and nowhere else
(``tests/test_sender_belt.py`` walks the AST and fails CI otherwise). So *"which business is this
going out as?"* is not a question a caller can forget to ask — it is the only way to obtain a sender
at all.

The precedence itself is NOT re-implemented here. :func:`resolve_infra_credential` already owns it
("the business's own wins; the instance's is the default"), so this module *feeds* it, deciding the
one thing that function cannot: whether an instance default may be offered for this provider **at
all**.

.. rubric:: ==The classification, and why ``credential_class`` was not enough==

``credential_class`` answers *"does it move money?"*. A sender asks a different question, and
answering it with the money one is how the WhatsApp leak survived B-03: SMTP and WhatsApp are BOTH
``INFRA``, so both inherited the instance-default fallback — and only one of them should have.

The distinction is not a matter of degree, and it is legible in the senders themselves:

* an **SMTP relay is a PIPE** (:attr:`InstanceFallback.LENT_TRANSPORT`). The identity travels
  per-message, in the ``From`` header, and ``SmtpEmailSender.send`` stamps it only when it is
  *unset* — so a business's mail goes through the operator's relay **as the business**. Lending a
  pipe is "infrastructure the operator lends", exactly as ``docs/byok-credentials.md`` has always
  said, and a single-business self-hoster who set ``AETHERCAL_SMTP_*`` once is entitled to have it
  go on working;
* a **WhatsApp/Twilio account IS AN IDENTITY** (:attr:`InstanceFallback.OPERATOR_IDENTITY`). There
  is no per-message ``From`` to stamp: the number is what the recipient sees, and what they reply
  to. Lending it does not put the business's message through the operator's pipe — ==it sends the
  message AS the operator==, to a stranger who can then reply to the wrong company.

So the operator's phone identity is **not lent by default**. An instance that upgrades stops leaking
without its operator doing anything, which is the only defensible direction for that default to
point.

.. rubric:: The escape hatch for the self-hoster, and why it is a DECLARATION

One business on one instance IS its operator, and for them ``AETHERCAL_WHATSAPP_*`` is their own
number — refusing to use it would be pedantry with a real cost. So the lending is available, via
:data:`LEND_OPERATOR_PHONE_IDENTITY_ENV`, it is **off unless the operator says otherwise**, and it
says so at boot (:func:`warn_if_operator_identity_is_lent` — the shape
``warn_if_loopback_is_allowlisted`` already established for exactly this kind of "legitimate on a
self-host, dangerous by default" choice).

==The bug today is that the lending happens by OMISSION.== A flag does not re-open it: it turns an
assumption nobody made into a sentence somebody had to write down.

.. rubric:: What a business with no credential gets — and why it is not an error

Nothing goes out on that channel for that business: it is simply absent from
:attr:`TenantSenders.channels`, and the drain retires the step as
:class:`~aethercal.server.services.outbox.OutboxSkipped`. That is terminal, it costs no attempt, and
it is ==visible in three places==: the outbox row settles to ``status='skipped'``, the worker logs
the reason at WARNING, and ``/metrics`` counts it under ``outcome="skipped"``. The reminder that did
not go out is a fact somebody can find — which is the whole difference between this and a silent
no-op.

Deliberately NOT a hard failure: ``services/outbox.OutboxSkipped`` already argues the case — an
unconfigured channel retried like an outage burns six attempts of exponential backoff, lands in the
dead-letter, and the message still does not arrive. That is noise instead of an answer.

.. rubric:: ==The residual, stated rather than left to be discovered==

The **reason is not on the row.** ``Outbox`` has no column for it, so ``status='skipped'`` is
queryable and *why* is only in the worker's log. For an unconfigured channel that is tolerable —
there are exactly two reasons and this module logs both by name — but it is a real gap, and it is
the OPERATOR's: somebody asking "why did this business's reminder not go out?" has to go and grep.
Closing it properly means a ``skip_reason`` column on ``Outbox`` and the drain writing the
exception's text at settle time. That is a migration, and it belongs to whoever next touches that
table rather than to a cut about senders.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.integrations.messaging.guard import DailyCaps, PhoneChannelSender
from aethercal.server.integrations.sms.config import TwilioConfig
from aethercal.server.integrations.sms.sender import TwilioSmsSender
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender
from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialError,
    CredentialProvider,
    CredentialSource,
    ResolvedCredential,
    WrongCredentialClassError,
    credential_class,
    resolve_infra_credential,
)
from aethercal.server.webhooks.allowlist import NO_PRIVATE_TARGETS
from aethercal.server.webhooks.ssrf import BlockedUrlError, Resolver, assert_target_allowed

_logger = logging.getLogger(__name__)

LEND_OPERATOR_PHONE_IDENTITY_ENV = "AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY"
"""Opt-in: may a business with no phone credential send from the OPERATOR's number?

Default **off**. It exists for the single-business self-hoster, for whom the operator's number and
the business's number are the same object. On an instance serving more than one business, turning it
on means every business without its own credential messages guests from a number none of them own.
"""

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})

_WHATSAPP_CAP_PREFIX = "WHATSAPP"
_SMS_CAP_PREFIX = "SMS"

_DEFAULT_SMTP_PORT = 587
"""The submission port, matching :class:`SmtpConfig`'s own default for an unset ``port``."""

_REQUIRED_TENANT_SCHEME = "https"
"""What a TENANT-supplied endpoint must speak. The operator's own may still be http — see
:func:`_assert_target_reachable`; the difference is provenance, not taste."""

_DEFAULT_BASE_URLS: Mapping[CredentialProvider, str] = {
    CredentialProvider.WHATSAPP: "",
    CredentialProvider.SMS: "https://api.twilio.com",
}
"""The endpoint a phone provider uses when its credential names none.

Only Twilio has one: its API lives at a fixed public address, and the override exists so an operator
can point at a regional edge or a local mock. Evolution is self-hosted, so there is no default to
have — an empty string fails the scheme check, which is the correct answer for a WhatsApp credential
with no ``base_url``: ``required_fields`` demands one, so this is unreachable except from a row
written before that rule, and it must not silently dial anywhere."""


class UnusableCredentialError(CredentialError):
    """A stored credential has every field it needs, and one of those fields cannot be USED.

    ==Distinct from :class:`IncompleteCredentialError`, and the gap between them is real.==
    ``store_credential`` refuses a credential MISSING a required field — but ``port`` is *optional*
    for SMTP, so ``{"host": …, "from_addr": …, "port": "abc"}`` passes the door happily. Nothing
    notices until the worker builds the sender, hours later, mid-drain, where ``int("abc")`` throws
    a bare ``ValueError``: a trace naming neither the business, nor the provider, nor the field.

    Validating every value-shape at the door is the tempting fix, and it does not work: the door
    would have to know every provider's optional schema, the rows already stored predate any such
    check, and a credential can be written by an older CLI or restored from a backup. ==A read-side
    guard is the only one that covers a row this process did not write.== (Same argument, and same
    shape, as :class:`MalformedCredentialError` — the read-side guard for a payload that is not an
    object at all.)

    .. rubric:: It is RETRYABLE, and that is a decision

    It is NOT an :class:`~aethercal.server.services.outbox.OutboxSkipped`. That is TERMINAL, and its
    own docstring says terminal "means IRREVERSIBLE, so it may only carry a condition that cannot be
    undone". A broken credential is undoable in one command — ``aethercal-admin credentials set`` —
    so retiring the step would destroy a reminder the fix would otherwise have delivered, which is
    the exact trap the paused-rule case already documents.

    So it propagates, the drain's ``except Exception`` fails the item, and it backs off toward the
    dead-letter. Every one of those six attempts is a chance for the fix to land in time, and the
    dead-letter is this product's channel for *"a human is needed"* — which is true here, and is
    precisely NOT true of a channel the operator simply never configured.
    """


def _unusable_message(provider: CredentialProvider, *, field: str, expected: str) -> str:
    """The refusal, naming the provider and the FIELD — and ==never the value==.

    The value is not echoed even though a port looks harmless. A credential's fields are secret, and
    a rule that holds only for the fields somebody judged boring is not a rule: the next field
    through here is a password.
    """
    return (
        f"the stored {provider.value} credential's {field!r} is not usable: it must be {expected}. "
        "The credential is not incomplete — this field is optional, so it was accepted when it was "
        "stored, and only a send can discover that its value cannot be used. Re-enter it with "
        f"`aethercal-admin credentials set --provider {provider.value}`. (The value is not shown — "
        "a credential's fields are secret, whichever one is at fault.)"
    )


class InstanceFallback(StrEnum):
    """Whether this provider's instance-wide configuration may stand in for a business's own.

    ==Both members are INFRA.== The split is not money-vs-sending (``credential_class`` owns that);
    it is *what the operator's configuration IS* — see the module docstring.
    """

    LENT_TRANSPORT = "lent_transport"
    """A pipe. The identity travels per message, so lending it sends the business's message AS the
    business. The instance default is legitimate."""

    OPERATOR_IDENTITY = "operator_identity"
    """The account IS the sender. Lending it sends the business's message AS THE OPERATOR — so it is
    not lent unless the operator has explicitly said to."""


def instance_fallback(provider: CredentialProvider) -> InstanceFallback:
    """What kind of thing this provider's instance configuration is. ==Exhaustive.==

    The ``assert_never`` is the load-bearing part, and its absence is exactly how the WhatsApp leak
    survived B-03. Add a sending provider without classifying it here and it does not type-check —
    rather than inheriting whatever the previous branch happened to be and quietly acquiring the
    right to send from somebody else's account.

    Raises :class:`WrongCredentialClassError` for a MONEY provider. It has no answer here: this
    function classifies *instance defaults*, and the money path's guarantee is that no code path
    exists which could offer it one. Returning anything at all would be the first step to a caller
    reading it generously.
    """
    if credential_class(provider) is not CredentialClass.INFRA:
        raise WrongCredentialClassError(
            f"{provider.value} handles money, so it has no instance default to classify. There is "
            "no fallback on the money path — see resolve_money_credential — and this function "
            "exists to decide which INFRA providers may have one."
        )
    match provider:
        case CredentialProvider.SMTP:
            return InstanceFallback.LENT_TRANSPORT
        case CredentialProvider.WHATSAPP | CredentialProvider.SMS:
            return InstanceFallback.OPERATOR_IDENTITY
        case (  # pragma: no cover - unreachable while credential_class stays exhaustive
            CredentialProvider.STRIPE | CredentialProvider.MERCADO_PAGO
        ):
            raise WrongCredentialClassError(f"{provider.value} handles money")
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def channel_for(provider: CredentialProvider) -> Channel:
    """The delivery channel this sending provider serves. ==Exhaustive, for the same reason.==

    The drain's registry is keyed by :class:`Channel`; the credentials are keyed by
    :class:`CredentialProvider`. This is the one place those two vocabularies meet, so that a
    provider cannot be added on one side only — which would let a business store a credential for a
    channel nothing ever reads: configured, and silent.
    """
    match provider:
        case CredentialProvider.SMTP:
            return Channel.EMAIL
        case CredentialProvider.WHATSAPP:
            return Channel.WHATSAPP
        case CredentialProvider.SMS:
            return Channel.SMS
        case (  # pragma: no cover - unreachable while credential_class stays exhaustive
            CredentialProvider.STRIPE | CredentialProvider.MERCADO_PAGO
        ):
            raise WrongCredentialClassError(
                f"{provider.value} handles money; it delivers no message and has no channel."
            )
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class _SenderSpec:
    """What ONE sending provider is made of: its instance config type, and its live client type.

    ==This table is what makes the belt derive itself.== ``tests/test_sender_belt.py`` reads these
    two types out of :data:`_SPECS` and asserts, against the AST of the whole server source, that
    nobody outside this module constructs either one. It never names them itself.

    So the fourth sending provider — the one an enumeration always misses — is covered the day it is
    added: :func:`instance_fallback` and :func:`channel_for` will not type-check without a branch
    for it, ``test_every_sending_provider_has_a_spec`` will not pass without an entry here, and the
    moment there is an entry, the locks cover its classes with nobody having remembered they exist.
    """

    config_type: type
    """The frozen dataclass that holds this provider's connection details."""

    sender_type: type
    """The live client. ==The object that decides whose account a message leaves on.=="""


_SPECS: Mapping[CredentialProvider, _SenderSpec] = {
    CredentialProvider.SMTP: _SenderSpec(config_type=SmtpConfig, sender_type=SmtpEmailSender),
    CredentialProvider.WHATSAPP: _SenderSpec(
        config_type=EvolutionConfig, sender_type=EvolutionWhatsAppSender
    ),
    CredentialProvider.SMS: _SenderSpec(config_type=TwilioConfig, sender_type=TwilioSmsSender),
}
"""Every sending provider, and the two types it is built from. ==Kept complete by a test.==

``test_every_sending_provider_has_a_spec`` asserts this covers exactly the ``INFRA`` half of
:class:`CredentialProvider`. A provider missing from here is not a small gap: it is a channel that
resolves to nothing for every business on the instance, silently — and one the AST belt would not
know to look for."""


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise RuntimeError(f"{LEND_OPERATOR_PHONE_IDENTITY_ENV} must be a boolean, got {raw!r}.")


def _optional_caps(environ: Mapping[str, str], *, prefix: str) -> DailyCaps | None:
    """This channel's caps if the operator declared them, else ``None``. ==Read INDEPENDENTLY.==

    ``EvolutionConfig.from_env`` reads the caps too — but only once it already knows the instance's
    own credentials are present. A business bringing its OWN WhatsApp to an instance where the
    operator runs none would therefore reach the send with no ceiling at all, and
    :class:`PhoneChannelSender` makes an uncapped phone sender unrepresentable. So the caps are read
    here on their own: they are the OPERATOR's abuse policy over the public booking form, and that
    form is the operator's surface no matter whose account pays the bill.

    ``None`` is fail-closed by construction — :func:`_build_phone_sender` cannot build without caps,
    so the channel is absent and its steps skip with a reason naming the variables to set.
    """
    try:
        return DailyCaps.from_env(environ, prefix=prefix)
    except RuntimeError:
        return None


@dataclass(frozen=True, slots=True)
class InstanceSenderDefaults:
    """The operator's own sending configuration — read ONCE, at the process edge (RF-19).

    ==Not "the credentials". The DEFAULTS.== Which of them a given business is actually allowed to
    reach is :func:`instance_fallback`'s decision, applied per provider in
    :func:`resolve_tenant_senders`.
    """

    smtp: SmtpConfig | None
    whatsapp: EvolutionConfig | None
    sms: TwilioConfig | None
    phone_caps: Mapping[CredentialProvider, DailyCaps]
    """The ceilings the operator declared per phone channel, read independently of the credentials.

    A business's OWN phone sender is capped by these too: the cap protects the stranger whose number
    somebody typed into the operator's public form, and that harm does not change owner along with
    the API key. The COUNTING was already per-business (``phone_sends_in_window`` filters by
    ``tenant_id``); only the ceiling's value is the operator's policy."""

    lend_operator_phone_identity: bool
    """Whether a business with no phone credential may send from the OPERATOR's number.

    Off unless declared. See :data:`LEND_OPERATOR_PHONE_IDENTITY_ENV`."""

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> InstanceSenderDefaults:
        """Read every ``AETHERCAL_*`` sending default. ==Keeps the existing boot contracts intact.==

        * SMTP absent → ``None`` (the transactional email is skipped, never a boot failure), exactly
          what the retired ``build_email_sender`` did;
        * a HALF-configured phone channel still RAISES out of ``from_env`` and fails the boot. That
          contract is the reason ``build_channel_senders`` existed, and it is preserved here rather
          than lost with it: "sending, but uncapped" must never be a state a process can reach.
        """
        try:
            smtp = SmtpConfig.from_env(environ)
        except RuntimeError:
            smtp = None

        caps: dict[CredentialProvider, DailyCaps] = {}
        whatsapp_caps = _optional_caps(environ, prefix=_WHATSAPP_CAP_PREFIX)
        if whatsapp_caps is not None:
            caps[CredentialProvider.WHATSAPP] = whatsapp_caps
        sms_caps = _optional_caps(environ, prefix=_SMS_CAP_PREFIX)
        if sms_caps is not None:
            caps[CredentialProvider.SMS] = sms_caps

        return cls(
            smtp=smtp,
            whatsapp=EvolutionConfig.from_env(environ),
            sms=TwilioConfig.from_env(environ),
            phone_caps=caps,
            lend_operator_phone_identity=_parse_bool(
                environ.get(LEND_OPERATOR_PHONE_IDENTITY_ENV), default=False
            ),
        )

    def secrets_for(self, provider: CredentialProvider) -> Mapping[str, str] | None:
        """This provider's instance default, in the same field shape a stored credential uses.

        Deliberately the SAME shape, so the instance default and a business's own credential go
        down the identical code path. Two paths would be two chances for one of them to skip a
        check the other applies.
        """
        match provider:
            case CredentialProvider.SMTP:
                if self.smtp is None:
                    return None
                secrets = {
                    "host": self.smtp.host,
                    "from_addr": self.smtp.from_addr,
                    "port": str(self.smtp.port),
                    "use_tls": "true" if self.smtp.use_tls else "false",
                }
                if self.smtp.username is not None:
                    secrets["username"] = self.smtp.username
                if self.smtp.password is not None:
                    secrets["password"] = self.smtp.password
                return secrets
            case CredentialProvider.WHATSAPP:
                if self.whatsapp is None:
                    return None
                return {
                    "base_url": self.whatsapp.base_url,
                    "instance": self.whatsapp.instance,
                    "api_key": self.whatsapp.api_key,
                }
            case CredentialProvider.SMS:
                if self.sms is None:
                    return None
                return {
                    "account_sid": self.sms.account_sid,
                    "auth_token": self.sms.auth_token,
                    "from_number": self.sms.from_number,
                    "base_url": self.sms.base_url,
                }
            case (  # pragma: no cover - unreachable: instance_fallback gates every caller
                CredentialProvider.STRIPE | CredentialProvider.MERCADO_PAGO
            ):
                raise WrongCredentialClassError(
                    f"{provider.value} handles money and has no instance default."
                )
            case _ as unreachable:  # pragma: no cover - unreachable while the match is exhaustive
                assert_never(unreachable)


def warn_if_operator_identity_is_lent(defaults: InstanceSenderDefaults) -> None:
    """Say it once, at boot, if the operator's own number may be lent to a business.

    The shape ``warn_if_loopback_is_allowlisted`` established: a legitimate choice on a
    single-business self-host, and the widest one available — so it is stated rather than left to be
    a default nobody noticed. On a multi-business instance it means a business's guest is messaged
    from, and replies to, a number that business does not own.
    """
    if not defaults.lend_operator_phone_identity:
        return
    lent = [
        provider.value
        for provider in (CredentialProvider.WHATSAPP, CredentialProvider.SMS)
        if defaults.secrets_for(provider) is not None
    ]
    if not lent:
        return
    _logger.warning(
        "%s is on: a business with no phone credential of its own will send from the OPERATOR's "
        "%s account. On a single-business instance that is the same number and this is fine. On an "
        "instance serving more than one business it means a guest is messaged from — and replies "
        "to — a number that business does not own. Configure each business's own credential "
        "(`aethercal-admin credentials set`) and switch this off.",
        LEND_OPERATOR_PHONE_IDENTITY_ENV,
        "/".join(lent),
    )


@dataclass(frozen=True, slots=True)
class TenantSenders:
    """The senders ONE business may send with, right now. ==Nothing here belongs to anybody else.==

    Built by :func:`resolve_tenant_senders` for one ``tenant_id``, and never cached across
    businesses. ``email`` is ``None`` and a channel is absent when that business has no credential
    and no default it is allowed to reach — which is "this channel is off for this business", the
    state the drain already knows how to retire with a reason.
    """

    tenant_id: uuid.UUID
    """WHOSE senders these are. Carried so a mix-up is detectable rather than invisible."""

    email: EmailSender | None
    channels: Mapping[Channel, PhoneChannelSender]

    @classmethod
    def for_offline_tests(
        cls,
        *,
        email: EmailSender | None = None,
        channels: Mapping[Channel, PhoneChannelSender] | None = None,
    ) -> Callable[[uuid.UUID], Awaitable[TenantSenders]]:
        """A resolver that hands the SAME senders to every business. ==THE OFFLINE HARNESS ONLY.==

        The offline suite drives the drain against recording fakes and a SQLite database that has no
        credential rows and no environment. It needs a resolver, and what it is testing is almost
        never *whose* sender came back — so this exists rather than nine copies of the same four
        lines across the suite.

        ==In the product it would be the B-03bis bug, restored, in one line.== "The same senders for
        every business" is the precise description of what ``app.state.email_sender`` was. So it is
        nailed shut on the side that matters: ``test_sender_belt.py`` asserts the shipped source
        never calls it. The precedent — and the reason this shape is trusted here — is
        ``WorkerPools.for_offline_tests``, which is fenced off exactly the same way for exactly the
        same reason.

        The ``tenant_id`` is still carried onto each result, so a test that DOES care can assert on
        it.
        """
        registry = dict(channels or {})

        async def _resolve(tenant_id: uuid.UUID) -> TenantSenders:
            return cls(tenant_id=tenant_id, email=email, channels=registry)

        return _resolve


def _credential_port(raw: str | None) -> int:
    """The stored ``port``, or the submission default. ==A bad one is a domain error, not a crash.==

    ``int()`` on ``"abc"`` raises a bare ``ValueError`` from inside the worker, mid-drain: a stack
    trace that names neither the business, nor the provider, nor the field. See
    :class:`UnusableCredentialError` for why this cannot be caught at the door instead.
    """
    if not raw:
        return _DEFAULT_SMTP_PORT
    try:
        return int(raw)
    except ValueError as exc:
        raise UnusableCredentialError(
            _unusable_message(CredentialProvider.SMTP, field="port", expected="a whole number")
        ) from exc


def _credential_bool(raw: str | None, *, field: str, default: bool) -> bool:
    """A stored boolean field. ==Names ITS OWN field — never an environment variable.==

    Deliberately not :func:`_parse_bool`, and that separation is the fix for a real defect: that
    function exists for :data:`LEND_OPERATOR_PHONE_IDENTITY_ENV` and hardcodes that variable's name
    in its error. Reused here it told an operator their lend-identity FLAG was malformed when what
    was actually wrong was one field of one business's stored SMTP credential — a variable they may
    never have set, on the other side of the product from the fault. ==An error that misdirects is
    worse than one that only says "no".==
    """
    if not raw:
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise UnusableCredentialError(
        _unusable_message(
            CredentialProvider.SMTP, field=field, expected="a boolean (true/false, 1/0, yes/no)"
        )
    )


def _smtp_from_secrets(secrets: Mapping[str, str]) -> SmtpConfig:
    """An :class:`SmtpConfig` from the stored field shape.

    ``host`` / ``from_addr`` are guaranteed present by ``required_fields(SMTP)``, which
    ``store_credential`` enforces at the door. The OPTIONAL fields are not guaranteed anything, and
    that is exactly where :class:`UnusableCredentialError` lives.
    """
    return SmtpConfig(
        host=secrets["host"],
        from_addr=secrets["from_addr"],
        port=_credential_port(secrets.get("port")),
        username=secrets.get("username") or None,
        password=secrets.get("password") or None,
        use_tls=_credential_bool(secrets.get("use_tls"), field="use_tls", default=True),
    )


@dataclass(frozen=True, slots=True)
class _EgressTarget:
    """==A WITNESS: this ``base_url`` has been through the egress guard.==

    It carries no cleverness. Its whole job is to be **unforgeable outside**
    :func:`_assert_target_reachable` — the only function that constructs one — so that
    :func:`_build_phone_sender` can take it as a required argument and thereby ==cannot be called
    with an unvalidated URL at all==.

    That is the idiom the rest of this batch runs on, and the reason to spend a class on it:
    ``PhoneChannelSender`` makes an uncapped sender unrepresentable; ``resolve_money_credential``
    has
    no ``instance_default`` parameter to pass. A check somebody must remember to call is not a
    guard.
    A parameter they cannot fabricate is.
    """

    url: str
    """The validated URL, normalised. ==Read this, never ``secrets["base_url"]``.==

    Taking it off the witness rather than back out of the credential is what stops the guard being
    decorative: validate one string and dial another, and the check was theatre."""


async def _assert_target_reachable(
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    *,
    source: CredentialSource,
    default_url: str,
    resolver: Resolver | None,
) -> _EgressTarget:
    """Admit this provider's ``base_url``. ==The only constructor of :class:`_EgressTarget`.==

    .. rubric:: Why this exists — it is B-03bis's own bill

    Before this batch ``base_url`` came from the instance's environment: **operator configuration,
    trusted because the operator is the person running the process.** Putting it in a per-business
    credential turned it into ==input a third party controls and this server obeys==. The same
    movement that closed the isolation leak opened a door onto the internal network: a business — or
    whoever compromises its account — points ``base_url`` at ``169.254.169.254`` (the cloud metadata
    service, which hands out this instance's own IAM credentials), at ``127.0.0.1``, or at the
    operator's LAN, and **we make the request for them**, with our reachability instead of theirs.

    .. rubric:: ==PROVENANCE decides, exactly as it does for the identity==

    A ``CredentialSource.INSTANCE`` URL is the operator configuring their own instance, with the
    same
    hands that hold the app secret. A self-hoster running Evolution on ``http://192.168.1.50`` is
    not
    attacking themselves; putting them through this guard would treat the operator as their own
    threat model and break a real deployment for nothing. A ``TENANT`` URL is third-party input.
    Same
    field, different provenance, different rule — the same distinction :func:`instance_fallback`
    draws about the identity.

    .. rubric:: What a tenant target must clear

    * **https.** Not hygiene: that request carries the business's own API key in a header, and it
      leaves the operator's network. (The operator's own URL may stay http — see above.)
    * **a PUBLIC address**, via ``assert_target_allowed`` with :data:`NO_PRIVATE_TARGETS`. ==The
      operator's allowlist is deliberately NOT passed.== It exists so the OPERATOR can send their
      own
      webhooks into their own LAN; a tenant reaching that LAN is the pivot itself. Widening this
      guard by the operator's own declaration would hand every business a key to it.
    * **by RESOLVED ADDRESS, never by hostname** — ``assert_target_allowed`` resolves and checks
      every record, so ``evil.example`` pointing at ``127.0.0.1`` is refused, and one poisoned
      record
      in a mixed answer poisons the whole target (no shopping for a good IP).

    Reusing that function rather than writing a second guard is the point: it already knows all of
    that, and a copy would have to rediscover every rule the hard way.
    """
    raw = secrets.get("base_url") or default_url
    if source is not CredentialSource.TENANT:
        return _EgressTarget(url=raw.rstrip("/"))

    if urlsplit(raw).scheme != _REQUIRED_TENANT_SCHEME:
        raise UnusableCredentialError(
            _unusable_message(
                provider,
                field="base_url",
                expected="an https URL (a tenant's endpoint carries its API key off this network)",
            )
        )
    try:
        await assert_target_allowed(raw, resolver=resolver, allowlist=NO_PRIVATE_TARGETS)
    except BlockedUrlError as exc:
        # ==Refused, and the message names the FIELD and never the value.== Same rule as every other
        # credential error here: a URL looks harmless, and the next field through this branch is a
        # password. The cause is chained so the operator's own log keeps the detail — they hold the
        # app secret and can decrypt any credential by design, so their log is not a new disclosure.
        raise UnusableCredentialError(
            _unusable_message(
                provider,
                field="base_url",
                expected=(
                    "a public internet address. It resolves somewhere inside this instance's own "
                    "network — loopback, link-local (including the cloud metadata service), or a "
                    "private range — and this server does not make that request on a business's "
                    "behalf"
                ),
            )
        ) from exc
    # TargetUnresolvable deliberately flies: DNS being down is a NETWORK failure, not a policy one,
    # and the drain must retry it rather than write the credential off as broken.
    return _EgressTarget(url=raw.rstrip("/"))


def _build_phone_sender(
    provider: CredentialProvider,
    secrets: Mapping[str, str],
    *,
    target: _EgressTarget,
    caps: DailyCaps,
    http_client: httpx.AsyncClient,
) -> PhoneChannelSender:
    """The live phone sender for ``provider``. ==Both its guarantees are TYPES, not checks.==

    ``caps`` makes an uncapped phone sender unrepresentable (:class:`PhoneChannelSender`), and
    ``target`` makes an unvalidated destination unrepresentable: an :class:`_EgressTarget` can only
    come from :func:`_assert_target_reachable`, so there is no shape of this program in which a
    tenant's URL is dialed without having been through the egress guard.

    ==The URL comes off the witness, never back out of ``secrets``.== Validating one string and then
    dialing another is how a guard becomes decoration.
    """
    match provider:
        case CredentialProvider.WHATSAPP:
            return EvolutionWhatsAppSender(
                EvolutionConfig(
                    base_url=target.url,
                    instance=secrets["instance"],
                    api_key=secrets["api_key"],
                    caps=caps,
                ),
                http_client,
            )
        case CredentialProvider.SMS:
            return TwilioSmsSender(
                TwilioConfig(
                    account_sid=secrets["account_sid"],
                    auth_token=secrets["auth_token"],
                    from_number=secrets["from_number"],
                    caps=caps,
                    base_url=target.url,
                ),
                http_client,
            )
        case _:  # pragma: no cover - only the phone providers reach here
            raise WrongCredentialClassError(f"{provider.value} is not a phone channel")


async def _resolve_one(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: CredentialProvider,
    fernet_key: bytes | Sequence[bytes],
    defaults: InstanceSenderDefaults,
) -> ResolvedCredential | None:
    """This business's credential for ``provider``, or the instance's IF it may have it.

    ==The one decision this module adds on top of B-03.== The precedence itself
    (``the business's own wins``) is :func:`resolve_infra_credential`'s, and is not restated here:
    this only chooses what to OFFER it as the default, which is the question that function cannot
    answer because it does not know what kind of thing the default is.
    """
    fallback = instance_fallback(provider)
    if fallback is InstanceFallback.OPERATOR_IDENTITY and not defaults.lend_operator_phone_identity:
        # ==No default is even offered.== Not "offered and then rejected": there is nothing for
        # resolve_infra_credential to fall back TO, so the only credential it can return is this
        # business's own.
        instance_default = None
    else:
        instance_default = defaults.secrets_for(provider)

    return await resolve_infra_credential(
        session,
        tenant_id=tenant_id,
        provider=provider,
        fernet_key=fernet_key,
        instance_default=instance_default,
    )


async def resolve_tenant_senders(  # noqa: PLR0913 - one keyword per injected seam (keys/env/net)
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    fernet_key: bytes | Sequence[bytes],
    defaults: InstanceSenderDefaults,
    http_client: httpx.AsyncClient,
    resolver: Resolver | None = None,
) -> TenantSenders:
    """==The funnel.== The senders ONE business sends with. There is no way to ask for "a sender".

    ``tenant_id`` is a required keyword with no default, and every live sending client in the
    product is constructed inside this module (pinned by ``tests/test_sender_belt.py``, which walks
    the source). Together those two facts are the belt: a caller cannot obtain a sender without
    saying whose it is, and cannot go around this function to build one.

    ``resolver`` is the DNS seam the egress guard uses (:func:`_assert_target_reachable`), injected
    so the whole path is deterministic and offline under test; production passes ``None`` and gets
    the real ``getaddrinfo``. It is the same seam ``webhooks.ssrf`` takes, for the same reason.

    ``session`` must be bound to ``tenant_id`` — the drain calls this inside its per-item
    ``tenant_scope`` on the RLS pool, so the credential is read under exactly that business's
    authority. The ``tenant_id`` filter inside ``_row_for`` is the second, independent reason the
    wrong row cannot come back.

    ``fernet_key`` is the rotation READER (current + retiring), so a credential the rotation has not
    reached yet still sends.

    A channel a business has no credential for — and no default it is allowed to reach — is simply
    ABSENT. That is "off for this business", and the drain retires its steps with a reason
    (:class:`~aethercal.server.services.outbox.OutboxSkipped`); it is never a failure, and never a
    silent success.
    """
    email: EmailSender | None = None
    smtp_credential = await _resolve_one(
        session,
        tenant_id=tenant_id,
        provider=CredentialProvider.SMTP,
        fernet_key=fernet_key,
        defaults=defaults,
    )
    if smtp_credential is not None:
        email = SmtpEmailSender(_smtp_from_secrets(smtp_credential.secrets))

    channels: dict[Channel, PhoneChannelSender] = {}
    for provider in (CredentialProvider.WHATSAPP, CredentialProvider.SMS):
        credential = await _resolve_one(
            session,
            tenant_id=tenant_id,
            provider=provider,
            fernet_key=fernet_key,
            defaults=defaults,
        )
        if credential is None:
            continue
        caps = defaults.phone_caps.get(provider)
        if caps is None:
            # ==Fail-closed, loudly.== The business HAS a credential; the operator has not declared
            # the ceilings for the public form it sits behind. Building an uncapped sender is the
            # one state `PhoneChannelSender` exists to make unrepresentable, so the channel stays
            # off — and says exactly which variables would turn it on, because a channel that is
            # silently absent is indistinguishable from one nobody wanted.
            _logger.warning(
                "business %s has a %s credential but this instance declares no daily caps for that "
                "channel, so it stays OFF: set AETHERCAL_%s_DAILY_CAP_PER_PHONE and "
                "AETHERCAL_%s_DAILY_CAP_PER_IP. The recipient comes from a PUBLIC form, so an "
                "uncapped channel can be made to message strangers on that business's account.",
                tenant_id,
                provider.value,
                provider.value.upper(),
                provider.value.upper(),
            )
            continue
        # ==THE EGRESS GUARD, and it runs BEFORE the sender exists.== A `_EgressTarget` cannot be
        # fabricated, and `_build_phone_sender` requires one — so a tenant's URL is never dialed
        # without having been through it. See `_assert_target_reachable`: this is the bill B-03bis
        # incurred by turning `base_url` from operator config into third-party input.
        target = await _assert_target_reachable(
            provider,
            credential.secrets,
            source=credential.source,
            default_url=_DEFAULT_BASE_URLS[provider],
            resolver=resolver,
        )
        if credential.source is CredentialSource.INSTANCE:
            _logger.warning(
                "business %s has no %s credential of its own and is sending from the OPERATOR's "
                "account, because %s is on. The guest will see, and reply to, a number this "
                "business does not own.",
                tenant_id,
                provider.value,
                LEND_OPERATOR_PHONE_IDENTITY_ENV,
            )
        channels[channel_for(provider)] = _build_phone_sender(
            provider, credential.secrets, target=target, caps=caps, http_client=http_client
        )

    return TenantSenders(tenant_id=tenant_id, email=email, channels=channels)


__all__ = [
    "LEND_OPERATOR_PHONE_IDENTITY_ENV",
    "InstanceFallback",
    "InstanceSenderDefaults",
    "TenantSenders",
    "UnusableCredentialError",
    "channel_for",
    "instance_fallback",
    "resolve_tenant_senders",
    "warn_if_operator_identity_is_lent",
]
