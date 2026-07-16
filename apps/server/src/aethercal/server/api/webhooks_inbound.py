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

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import Booking
from aethercal.server.deps import get_session
from aethercal.server.services.bookings import (
    BookingEffects,
    cancel_confirmed_booking_effects,
    confirm_paid_booking_effects,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.payment_webhooks import (
    PAYMENT_WEBHOOK_ADAPTERS,
    dispatch_payment_event,
    record_payment_event,
)
from aethercal.server.services.tenant_credentials import (
    CredentialError,
    CredentialProvider,
    resolve_money_credential,
)
from aethercal.server.services.tenant_resolution import tenant_by_slug
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


def _now() -> datetime:
    return datetime.now(UTC)


def _unauthorized() -> HTTPException:
    """The ONE answer for every way verification can fail — unknown business, no credential, bad
    signature. Deliberately indistinguishable: the endpoint tells a caller who could not sign
    nothing about which businesses exist or how they are set up."""
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="signature verification")


def _payload_too_large() -> HTTPException:
    """413 for a body over :data:`MAX_WEBHOOK_BODY_BYTES` — refused BEFORE any signature check or
    database work, and without buffering the whole body (finding 3)."""
    return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large")


async def _read_body_within_limit(request: Request) -> bytes:
    """Read the raw body, but NEVER more than :data:`MAX_WEBHOOK_BODY_BYTES` (finding 3).

    A declared ``Content-Length`` over the cap is rejected outright (the fast path); the STREAMED
    read is also capped, byte for byte, so a missing or lying length cannot smuggle a large body
    past the header check. Either way the body is never fully buffered before the limit is enforced,
    so the memory this endpoint can be made to allocate is bounded by the cap."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="malformed Content-Length"
            ) from None
        if length > MAX_WEBHOOK_BODY_BYTES:
            raise _payload_too_large()
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

    adapters = getattr(request.app.state, "webhook_adapters", PAYMENT_WEBHOOK_ADAPTERS)
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
        raise _unauthorized() from None
    webhook_secret = credential.secrets.get(_WEBHOOK_SECRET_FIELD)
    if not webhook_secret:  # pragma: no cover - required_fields guarantees it, but fail closed
        raise _unauthorized()

    # (4) ==verify the HMAC over the RAW body. Invalid → 401, and NOTHING has been written.==
    if not adapter.verify_signature(
        raw_body=raw_body, secret=webhook_secret, headers=request.headers
    ):
        raise _unauthorized()

    # (5) only now: parse, record (idempotent / anti-replay), dispatch.
    # ==Finding 4.== The signature has ALREADY authorised this — it is genuinely from the provider.
    # An event we do not model (``customer.created``, …) is therefore ACKed 200 and NOT written, not
    # rejected: any non-2xx makes the provider retry it for ever. 401 (bad signature) is the only
    # rejection; there is no 400 here, because a verified body we choose not to act on is not an
    # error, it is an event we are simply not interested in.
    event = adapter.parse(raw_body)
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
    return {"status": "ok"}


__all__ = ["receive_payment_webhook", "router"]
