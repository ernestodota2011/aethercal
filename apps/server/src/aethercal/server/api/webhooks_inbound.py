"""``POST /webhooks/{provider}/{tenant_slug}`` — the inbound payment webhook (B-05b, §4.4).

==No API key. What authorises an event is its SIGNATURE, never the route.== The slug only SELECTS
which business's signing secret to check against; it confers no authority of its own. The order is
strict, and criterion 33 rides on it — an invalid signature is a 401 with ZERO writes:

1. read the RAW body (before anything parses it);
2. resolve the business from the ROUTE slug and BIND it (via the ``SECURITY DEFINER`` resolver,
   which works before any GUC exists);
3. read THAT business's signing secret from ``tenant_credentials`` (now under its own RLS);
4. verify the HMAC over the raw body — invalid → 401, and nothing has been written;
5. only THEN record the event (idempotent, anti-replay) and dispatch it to the arbiter.

An unknown slug and a missing/invalid credential all answer 401, deliberately: the endpoint reveals
nothing about which businesses exist or how they are configured to a caller that could not sign.
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.db.guc import bind_tenant, reset_tenant_binding
from aethercal.server.db.models import Booking
from aethercal.server.deps import get_session
from aethercal.server.services.bookings import (
    BookingEffects,
    cancel_confirmed_booking_effects,
    confirm_paid_booking_effects,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.outbox import enqueue_guest_reply
from aethercal.server.services.payment_webhooks import (
    InboundWebhook,
    PaymentWebhookAdapter,
    dispatch_payment_event,
    record_payment_event,
)
from aethercal.server.services.phone_verification import get_suppression_key
from aethercal.server.services.templates import (
    REPLY_CANCEL_KIND,
    REPLY_CONFIRM_KIND,
)
from aethercal.server.services.tenant_credentials import (
    CredentialError,
    CredentialProvider,
    resolve_infra_credential,
    resolve_money_credential,
)
from aethercal.server.services.tenant_resolution import tenant_by_slug
from aethercal.server.services.tenant_senders import InstanceSenderDefaults
from aethercal.server.services.whatsapp_interactive import (
    WhatsAppProcessResult,
    WhatsAppReplyAction,
    extract_evolution_payload,
    extract_provider_message_id,
    process_inbound_whatsapp,
)
from aethercal.server.settings import Settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderPath = Annotated[str, Path(max_length=32, description="The payment provider")]
TenantSlug = Annotated[str, Path(max_length=63, description="The business's globally-unique slug")]

_WEBHOOK_SECRET_FIELD = "webhook_secret"

MAX_WEBHOOK_BODY_BYTES = 256 * 1024
"""The hard cap on the inbound body (finding 3). Stripe events are a few KB; 256 KiB is generous
headroom. ==This endpoint is UNAUTHENTICATED==, so an unbounded ``await request.body()`` would let a
caller who cannot sign anything exhaust the process's memory with one giant POST — a denial of
service that never reaches the signature check. The body is read with this cap instead."""

_ConfirmEffects = Callable[[AsyncSession, Booking, datetime], Awaitable[None]]

_logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _unauthorized() -> HTTPException:
    """The ONE answer for every way verification can fail - unknown business, no credential, bad
    signature. Deliberately indistinguishable: the endpoint tells a caller who could not sign
    nothing about which businesses exist or how they are set up."""
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="signature verification")


def _deny_after_binding() -> HTTPException:
    """The same 401, plus a RELEASE of the tenant binding opened to read the credential.

    The bind cannot be avoided — ``tenant_credentials`` and ``webhook_secrets`` are RLS-protected,
    so the very secret being compared is unreadable without the scope — but once the caller has
    failed to authenticate, nothing may continue under that business's authority. ``get_session``
    tears the scope down on the way out either way; doing it at every failure point makes the
    intent explicit instead of incidental, and covers a future handler that runs code after a
    caught HTTPException.
    """
    reset_tenant_binding()
    return _unauthorized()


def _payload_too_large() -> HTTPException:
    """413 for a body over :data:`MAX_WEBHOOK_BODY_BYTES` — refused BEFORE any signature check or
    database work, and without buffering the whole body (finding 3)."""
    return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large")


def _reject_an_oversized_declaration(request: Request) -> None:
    """The HEADER half of the size cap: a declared ``Content-Length`` over the cap is a 413 here.

    ==Split out of the read so an UNAUTHENTICATED caller cannot make the process buffer a body.==
    The money webhook MUST read the raw bytes before it can verify (the HMAC is over them), so its
    order is forced; the WhatsApp webhook authenticates with a header, so it can — and now does —
    check this, authenticate, and only THEN read the stream. Same cap, same 413, but the bytes of a
    request that cannot authenticate are never pulled into memory at all.
    """
    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        length = int(declared)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed Content-Length"
        ) from None
    if length > MAX_WEBHOOK_BODY_BYTES:
        raise _payload_too_large()


async def _read_body_within_limit(request: Request) -> bytes:
    """Read the raw body, but NEVER more than :data:`MAX_WEBHOOK_BODY_BYTES` (finding 3).

    A declared ``Content-Length`` over the cap is rejected outright (the fast path); the STREAMED
    read is also capped, byte for byte, so a missing or lying length cannot smuggle a large body
    past the header check. Either way the body is never fully buffered before the limit is enforced,
    so the memory this endpoint can be made to allocate is bounded by the cap."""
    _reject_an_oversized_declaration(request)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            raise _payload_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _confirm_effects(request: Request, settings: Settings) -> _ConfirmEffects:
    """The arbiter's ``confirm_effects``: the SAME confirmation chain a free booking runs.

    Built here, in the one layer that may import both the booking service and the arbiter — the
    arbiter never imports the booking-effects wiring itself.
    """
    base = settings.booking_base_url or str(request.base_url)
    effects = BookingEffects(
        signer=GuestTokenSigner(settings.app_secret), booking_base_url=base.rstrip("/")
    )

    async def _run(session: AsyncSession, booking: Booking, now: datetime) -> None:
        await confirm_paid_booking_effects(session, booking=booking, effects=effects, now=now)

    return _run


def _cancel_effects(request: Request, settings: Settings) -> _ConfirmEffects:
    """The arbiter's ``cancel_effects`` (r5 finding 2): the SAME cancellation chain a guest/host
    cancel runs, for an OUT-OF-BAND ``charge.refunded``.

    Built here — the one layer that may import both the booking service and the arbiter — so the
    out-of-band refund fires the full chain (webhook + CANCEL transition + Google DELETE + guest
    email) instead of a partial copy. The bundle is present, so the Google delete and the email are
    enqueued: an appointment cancelled by an external refund is owed both, like any cancellation.
    """
    base = settings.booking_base_url or str(request.base_url)
    effects = BookingEffects(
        signer=GuestTokenSigner(settings.app_secret), booking_base_url=base.rstrip("/")
    )

    async def _run(session: AsyncSession, booking: Booking, now: datetime) -> None:
        await cancel_confirmed_booking_effects(session, booking=booking, effects=effects, now=now)

    return _run


@router.post("/{provider}/{tenant_slug}", status_code=status.HTTP_200_OK)
async def receive_payment_webhook(
    provider: ProviderPath,
    tenant_slug: TenantSlug,
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
    """Receive, verify, record and apply one inbound payment event. See the module docstring for the
    order — it is the design, not an implementation detail."""
    # (1) the RAW body, before FastAPI parses anything from it — the HMAC is over these exact bytes.
    # ==Finding 3.== Read it under a hard size cap: this endpoint has no auth, so an unbounded read
    # is a memory-exhaustion DoS. An over-cap body is a 413 here, before any verification or write.
    raw_body = await _read_body_within_limit(request)

    # ==No fallback registry.== This used to default to a hand-written dict whose Mercado Pago entry
    # was a generic HMAC adapter that could never have verified a real Mercado Pago
    # notification. The
    # map is now built exhaustively from the MONEY credential providers
    # (``integrations.money.build_webhook_adapters``) and wired onto app state at startup; if it is
    # absent, payments are not configured and this refuses rather than reaching for a stand-in.
    adapters: dict[str, PaymentWebhookAdapter] | None = getattr(
        request.app.state, "webhook_adapters", None
    )
    if not adapters:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="payments are not configured"
        )
    adapter = adapters.get(provider)
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    try:
        credential_provider = CredentialProvider(provider)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider"
        ) from None

    # (2) resolve the business from the ROUTE slug and BIND it. Reads only — no authority yet.
    tenant_id = await tenant_by_slug(session, tenant_slug)
    if tenant_id is None:
        raise _unauthorized()
    await bind_tenant(session, tenant_id)

    # ==The same ONE try/finally as the WhatsApp handler, for the same reason.== It starts
    # right after the bind, so the release is a property of the shape: the 413 out of the size
    # cap, an adapter that raises while verifying or parsing, and every future edit are covered
    # without touching a list of exits.
    try:
        # (3) read THAT business's signing secret (BYOK, under its own RLS).
        settings: Settings = request.app.state.settings
        fernet_keys = request.app.state.fernet_keys
        try:
            credential = await resolve_money_credential(
                session,
                tenant_id=tenant_id,
                provider=credential_provider,
                fernet_key=fernet_keys,
            )
        except CredentialError:
            # No credential, or one that cannot be used to verify: we cannot authorise this event.
            raise _deny_after_binding() from None
        webhook_secret = credential.secrets.get(_WEBHOOK_SECRET_FIELD)
        if not webhook_secret:  # pragma: no cover - required_fields guarantees it, but fail closed
            raise _deny_after_binding()

        # (4) ==verify the provider's signature. Invalid → 401, and NOTHING has been written.==
        # The whole request goes to the adapter — body, headers AND query — because what a provider
        # signs is the provider's business: Stripe HMACs the raw body, Mercado Pago HMACs a manifest
        # built from the ``data.id`` QUERY parameter and the ``x-request-id`` header. The adapter is
        # handed the signing secret ALONE, never the credential that can move money.
        inbound = InboundWebhook(
            raw_body=raw_body,
            headers=request.headers,
            query=request.query_params,
        )
        if not adapter.verify_signature(inbound, secret=webhook_secret):
            # ==A 401 must not leave the request bound to a business it could not authenticate.==
            # The bind ABOVE is unavoidable — the signing secret is RLS-protected, so it cannot be
            # read without the scope — but nothing may continue under that authority. The finally
            # releases it; this call states the intent where authentication failed.
            raise _deny_after_binding()

        # (5) only now: parse, record (idempotent / anti-replay), dispatch.
        # ==Finding 4.== The signature has ALREADY authorised this — it is genuinely from the
        # provider.
        # An event we do not model (``customer.created``, …) is therefore ACKed 200 and NOT
        # written, not rejected: any non-2xx makes the provider retry it for ever. 401 (bad
        # signature) is the only rejection; there is no 400 here, because a verified body we
        # choose not to act on is not an error, it is an event we are simply not interested in.
        # ==``parse`` may perform provider I/O, and is given the business's own credential.==
        # Mercado Pago's notification carries an id and nothing else — no amount, no currency, no
        # status — and its signature does not cover the body anyway, so the adapter must fetch the
        # payment (``GET /v1/payments/{id}``) on the business's ``access_token`` to learn what
        # happened. Stripe's adapter ignores ``secrets`` and does no I/O: its signed body is
        # self-describing.
        event = await adapter.parse(inbound, secrets=credential.secrets)
        if event is None:
            return {"status": "ignored"}

        row, is_new = await record_payment_event(
            session, tenant_id=tenant_id, provider=provider, event=event
        )
        if not is_new:
            # A replay of the SAME event id — the UNIQUE already holds the first delivery. Ack it.
            return {"status": "duplicate"}

        await dispatch_payment_event(
            session,
            tenant_id=tenant_id,
            provider=provider,
            event=event,
            row=row,
            now=_now(),
            confirm_effects=_confirm_effects(request, settings),
            cancel_effects=_cancel_effects(request, settings),
        )
    finally:
        reset_tenant_binding()

    return {"status": "ok"}


@router.post("/whatsapp/{tenant_slug}", status_code=status.HTTP_200_OK)
@router.post("/evolution/{tenant_slug}", status_code=status.HTTP_200_OK)
async def receive_whatsapp_webhook(
    tenant_slug: TenantSlug,
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
    """Receive, authenticate, and process an inbound WhatsApp event from Evolution API (Horizon 1).

    Authentication checks the API key Evolution API presents in a HEADER (`apikey`, `x-api-key`, or
    `Authorization: Bearer <key>`) against the tenant's configured WhatsApp credential, or the
    instance default.

    ==Headers only, deliberately: a credential in the query string is a credential in every access
    log, proxy log and browser history between the provider and here== — and the provider sends a
    header just as happily. (An unknown slug, a missing credential and a wrong key all answer the
    same 401, so the endpoint is no oracle for which businesses exist.)

    ==Why a shared key and not a signature:== the money webhook verifies an HMAC because Stripe and
    Mercado Pago SIGN their events. Evolution API does not sign its webhooks — there is no HMAC to
    verify — so the tenant's own provider credential is the authentication, presented over TLS and
    compared in constant time. What an authenticated caller can reach is narrow by construction:
    the handler only ever acts on an UPCOMING booking whose phone matches the sender's (the
    intent parser cannot name a booking, and the suppression list has no read surface).

    .. rubric:: Every exit releases the tenant binding, and it says so here

    The bind is unavoidable (the credential is RLS-protected) and ``get_session`` tears the request
    scope down either way — but ==a reader should not have to follow two files to know the authority
    does not outlive the work==: one ``try/finally`` starts right after the bind and releases it on
    every exit this handler has, including the ones a branch audit forgets.
    """
    # ==The body is read AFTER authenticating.== The size cap is checked from the header first (a
    # declared oversize is still a 413 before any work), but the BYTES are pulled from the stream
    # only once the caller has proven it holds the tenant's provider key: an unauthenticated POST
    # must not be able to make the process buffer up to MAX_WEBHOOK_BODY_BYTES. (The money webhook
    # below reads first because its HMAC is over the raw bytes — the order there is forced; here it
    # is a choice, and this is the strictly better one.)
    _reject_an_oversized_declaration(request)

    tenant_id = await tenant_by_slug(session, tenant_slug)
    if tenant_id is None:
        raise _unauthorized()
    await bind_tenant(session, tenant_id)

    # ==ONE try/finally, right after the bind, so "the authority never outlives the work" is a
    # property of the SHAPE and not of a handful of scattered calls.== It covers what the explicit
    # resets did not: the 413 a size cap raises while reading the bytes, an unexpected error out of
    # the credential read, and anything a future edit adds between here and the exit. A reader no
    # longer has to audit every branch to know the binding is released.
    try:
        fernet_keys = request.app.state.fernet_keys
        defaults: InstanceSenderDefaults | None = getattr(
            request.app.state, "sender_defaults", None
        )
        instance_default = (
            defaults.secrets_for(CredentialProvider.WHATSAPP) if defaults is not None else None
        )

        try:
            credential = await resolve_infra_credential(
                session,
                tenant_id=tenant_id,
                provider=CredentialProvider.WHATSAPP,
                fernet_key=fernet_keys,
                instance_default=instance_default,
            )
        except CredentialError:
            raise _deny_after_binding() from None

        if credential is None:
            raise _deny_after_binding()

        expected_key = credential.secrets.get("api_key")
        if not expected_key:
            raise _deny_after_binding()

        auth_header = request.headers.get("authorization", "")
        bearer_token = (
            auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
        )
        received_key = (
            request.headers.get("apikey") or request.headers.get("x-api-key") or bearer_token
        )
        if not received_key or not hmac.compare_digest(received_key.strip(), expected_key.strip()):
            # ==A 401 must not leave the request bound to a business it could not authenticate.==
            # The bind above is unavoidable — the credential is RLS-protected and cannot be read
            # without the scope — but nothing may continue under that authority. The finally below
            # releases it; this explicit call states the intent at the exact point where
            # authentication failed.
            raise _deny_after_binding()

        # Authenticated: NOW the bytes are worth reading (still under the streamed cap).
        raw_body = await _read_body_within_limit(request)

        try:
            payload = json.loads(raw_body)
        except Exception as exc:
            # Acked as ignored (the provider gets a 200 and stops retrying), but NOT silently: an
            # operator wondering why a guest's "1" never landed needs this line.
            _logger.debug("inbound WhatsApp webhook body is not JSON (%s); ignoring", exc)
            return {"status": "ignored"}

        if not isinstance(payload, dict):
            return {"status": "ignored"}

        extracted = extract_evolution_payload(payload)
        if extracted is None:
            # ==A message the parser cannot use still leaves a trace.== An audio note, a sticker,
            # a group message, an event we do not model: the guest may believe they answered. The
            # API answer is `ignored` and nobody reads API answers, so the event NAME is logged
            # here (never the payload: it carries the sender's number and their words).
            _logger.info(
                "inbound WhatsApp webhook for tenant %s carried no guest text (event=%s); ignored",
                tenant_id,
                str(payload.get("event"))[:64],
            )
            return {"status": "ignored"}

        sender_phone, message_text = extracted

        settings: Settings = request.app.state.settings
        base = settings.booking_base_url or str(request.base_url)
        effects = BookingEffects(
            signer=GuestTokenSigner(settings.app_secret),
            booking_base_url=base.rstrip("/"),
        )

        # ==A failure here is a 5xx ON PURPOSE, and the retry it buys is SAFE.== Nothing in this
        # handler catches an error from the transition: ``get_session`` rolls the transaction
        # back on the way out, so a failed attempt leaves NO partial write, and every transition
        # this service performs is replay-tolerant — a second "1" keeps the first stamp, a second
        # "2" finds no CONFIRMED booking, a second STOP finds the suppression row already there.
        # Answering 200 with an "error" status would instead LOSE the guest's reply for ever on a
        # transient database hiccup. So let the provider retry as much as it likes: the worst
        # case is a duplicate that changes nothing.
        result = await process_inbound_whatsapp(
            session,
            tenant_id=tenant_id,
            sender_phone=sender_phone,
            message_text=message_text,
            suppression_key=get_suppression_key(settings.suppression_key),
            now=_now(),
            effects=effects,
        )
        await _queue_guest_acknowledgement(
            session, payload=payload, result=result, tenant_id=tenant_id
        )

    finally:
        # The single release, for every exit above: the returns, the raises, and the
        # cancellation of a request in flight alike. ``reset_tenant_binding`` is idempotent, so
        # the explicit calls a 401 still makes are an intent statement, not a second release.
        reset_tenant_binding()

    return {
        "status": result.status,
        "action": result.action.value,
    }


_REPLY_KIND_BY_ACTION: Mapping[WhatsAppReplyAction, str] = {
    WhatsAppReplyAction.CONFIRM_ATTENDANCE: REPLY_CONFIRM_KIND,
    WhatsAppReplyAction.CANCEL: REPLY_CANCEL_KIND,
}
"""What the guest gets acknowledged, and ==what they deliberately do not.==

An OPT_OUT is absent, and that is a decision with its own paragraph in
``services/templates``: the suppression IS the answer, and a "you have been unsubscribed"
message would be the first message sent to a number that just asked for silence. An UNKNOWN
gets nothing either — there is no reply the product can honestly give to "hola"."""


async def _queue_guest_acknowledgement(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    result: WhatsAppProcessResult,
    tenant_id: uuid.UUID,
) -> None:
    """Queue the acknowledgement of the guest's message, under the FULL outbound belt.

    ==It is queued, not sent here, and the difference is the whole point.== An outbound chat message
    is an outbound message: it has to pass consent, the opt-out list, the daily caps and the ledger.
    The webhook does not own any of that — the outbox does, for every channel and every effect — so
    this encodes nothing but WHICH reply, keyed so that a retried webhook (or a replayed drain)
    cannot send it twice.
    """
    kind = _REPLY_KIND_BY_ACTION.get(result.action)
    if kind is None or result.booking_id is None:
        return
    booking = await session.get(Booking, result.booking_id)
    if booking is None or booking.tenant_id != tenant_id:  # pragma: no cover - defensive
        return
    message_id = extract_provider_message_id(payload)
    # The provider's message id is what makes this exactly-once across BOTH retry layers. Without
    # one, the key falls back to the kind: at most one acknowledgement of that kind per booking,
    # which the ledger would enforce anyway.
    dedupe_key = f"reply:{kind}:{message_id}" if message_id else f"reply:{kind}"
    await enqueue_guest_reply(
        session,
        booking=booking,
        channel=Channel.WHATSAPP,
        kind=kind,
        dedupe_key=dedupe_key[:128],
    )


__all__ = ["receive_payment_webhook", "receive_whatsapp_webhook", "router"]
