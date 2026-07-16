"""The PUBLIC router — ==the endpoint with no key, and everything that stands in for one.==

The booking page used to hold ONE API key with the tenant's full permissions, in the most exposed
process in the system, and it could serve exactly one business because a key names exactly one. This
router deletes that key. The page becomes what it always should have been: an anonymous client of an
anonymous API, serving as many businesses as the instance hosts.

==Taking the authentication off a WRITE endpoint is the most dangerous change in this project.== So
every control that replaces the key ships in the SAME cut, never as a follow-up:

* **the resolver is the PAIR** ``(tenant_slug, event_slug)``, and it fails closed. The event slug is
  unique per BUSINESS (``UniqueConstraint("tenant_id","slug")``), so ``intro`` exists in every
  business on the instance; only ``tenants.slug`` is globally unique. Resolve by event slug alone
  and
  a guest's booking lands in a stranger's diary — and, from the payments cut on, a stranger's money
  in a stranger's Stripe account;
* **the captcha** — the only control that makes an attempt COST an attacker anything (a per-email
cap
  is beaten with an alias, a per-IP cap with a proxy pool). Verified server-side, fail-closed, and
  the process refuses to boot without its secret (``Settings``);
* **the address**, stamped on the booking through the declared proxy contract — which is what the
  per-IP daily cap, required at boot since RF-24, finally has to count;
* **the rate limit**, mounted over this prefix in ``create_app``. The API had none. Anywhere;
* **the projections are its own.** ``BookingRead`` is a PII dump (name, e-mail, notes, answers);
  ``EventTypeRead`` carries internal ids. Neither is reachable from here.

What is NOT here matters as much: ``GET /bookings`` — every booking a business ever took, each with
the guest's name, address and free-text notes on it — is not public, is not mounted under this
prefix, and never will be.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.branding import TenantBrandingRead
from aethercal.schemas.public import (
    PublicBookingCreate,
    PublicBookingRead,
    PublicEventTypeRead,
    PublicSlotsResponse,
)
from aethercal.schemas.slots import SlotRead
from aethercal.server.api.bookings import map_booking_error
from aethercal.server.api.slots import require_iana_zone_or_422, require_window_is_sane
from aethercal.server.client_ip import TrustedProxies, resolve_client_ip
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import EventType, Payment, PaymentStatus
from aethercal.server.deps import get_session
from aethercal.server.integrations.turnstile import TurnstileVerifier
from aethercal.server.services.bookings import (
    BookingEffects,
    BookingError,
    BookingParams,
    EventTypeInactiveError,
    EventTypeNotFoundError,
    create_booking,
)
from aethercal.server.services.branding import get_branding, public_branding
from aethercal.server.services.event_types import (
    get_bookable_event_type_by_slug,
    list_bookable_event_types,
)
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.payments import (
    HOLD_TTL,
    PaymentGateway,
    enqueue_expire_hold,
)
from aethercal.server.services.slots import compute_slots
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    MissingCredentialError,
    resolve_money_credential,
)
from aethercal.server.services.tenant_resolution import tenant_by_slug
from aethercal.server.services.workflow_rules import PhoneChannelScope, phone_channel_scope
from aethercal.server.settings import Settings

router = APIRouter(prefix="/public", tags=["public"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantSlug = Annotated[str, Path(max_length=63, description="The business's globally-unique slug")]
EventSlug = Annotated[str, Path(max_length=63, description="Its slug inside that business")]

_NOT_FOUND = "not_found"
_CAPTCHA_REQUIRED = "captcha_required"


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found() -> HTTPException:
    """The ONE answer for every way a public lookup can miss. Deliberately indistinguishable.

    An unknown business, an unknown event type, an event type belonging to a DIFFERENT business, and
    one this business has switched off all produce exactly this. Give any of them its own answer and
    the 404s become an oracle: a stranger enumerates which businesses live on this instance and
    which
    of their services were withdrawn — from an endpoint that asked them for nothing at all.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": _NOT_FOUND, "message": "Not found"},
    )


def _settings(request: Request) -> Settings:
    value: Settings = request.app.state.settings
    return value


def _client_ip(request: Request) -> str | None:
    """The guest's address, believed only as far as the declared proxy contract allows."""
    trusted: TrustedProxies = request.app.state.trusted_proxies
    return resolve_client_ip(request, trusted)


async def _bind_business(session: AsyncSession, tenant_slug: str) -> uuid.UUID:
    """Resolve the business from its slug and BIND it — before a single scoped row is touched.

    Through the ``SECURITY DEFINER`` resolver, because that is the only thing that works: with no
    business bound, row-level security shows this session nothing. And the moment it IS bound, every
    query that follows — the event type, the slots, the booking's INSERT, the outbox rows it queues
    —
    is confined to that business by the database rather than by this module's good intentions.
    """
    tenant_id = await tenant_by_slug(session, tenant_slug)
    if tenant_id is None:
        raise _not_found()
    await bind_tenant(session, tenant_id)
    return tenant_id


async def _resolve_event(session: AsyncSession, *, tenant_slug: str, event_slug: str) -> EventType:
    """==THE RESOLVER.== ``(tenant_slug, event_slug)`` → exactly one event type, or a 404.

    The order is forced, and it is not negotiable:

    1. **the business**, from its slug (globally unique — ``db/models/tenancy.py``), and BOUND;
    2. **only then the event type**, by its slug, INSIDE that business.

    Doing (2) without (1) is the bug this entire cut exists to prevent: the event slug is unique per
    business, so a bare lookup by slug finds one row for every business that happens to name a
    service the same way — and whichever row such a query returned would be somebody's booking filed
    in somebody else's diary.
    """
    tenant_id = await _bind_business(session, tenant_slug)
    event_type = await get_bookable_event_type_by_slug(
        session, tenant_id=tenant_id, slug=event_slug
    )
    if event_type is None:
        raise _not_found()
    return event_type


@router.get("/{tenant_slug}/event-types", response_model=list[PublicEventTypeRead])
async def list_public_event_types(
    tenant_slug: TenantSlug, session: SessionDep
) -> list[PublicEventTypeRead]:
    """The business's bookable services, as a guest sees them: no key, no internal ids.

    Only the ACTIVE ones. The page used to receive every event type and filter the withdrawn ones
    out
    in memory — but that is the CLIENT, and a server may never lean on its client to enforce what
    the
    business decided. With no key in front of this, "the page filters it" would BE the protection.
    """
    tenant_id = await _bind_business(session, tenant_slug)
    rows = await list_bookable_event_types(session, tenant_id=tenant_id)
    scope: PhoneChannelScope = await phone_channel_scope(session, tenant_id=tenant_id)
    return [
        PublicEventTypeRead.model_validate(row).model_copy(
            update={"collects_phone": scope.covers(row.id)}
        )
        for row in rows
    ]


@router.get("/{tenant_slug}/branding", response_model=TenantBrandingRead)
async def get_public_branding(tenant_slug: TenantSlug, session: SessionDep) -> TenantBrandingRead:
    """The business's public brand — display name, logo, accent colour, timezone — with NO key.

    ==The keyless twin of ``GET /api/v1/branding``.== That endpoint answers the business the API
    KEY authenticated as; the booking page holds no key any more, so it asks HERE, naming the
    business in the ROUTE — the same ``tenant_slug`` every other public endpoint resolves through
    ``_bind_business``, failing closed the same way: an unknown slug is the shared 404, and
    indistinguishable from every other public miss.

    The four columns are public by construction — the only fields of ``tenants`` a guest was ever
    meant to see (``schemas.branding``) — and ``public_branding`` is the projection: the registered
    ``name``, the ``slug`` and the id are not on the wire model at all, so nothing internal leaks
    from here. There is no PII on this endpoint because there is no booking on it — it answers
    "whose page is this?", and nothing more.
    """
    tenant_id = await _bind_business(session, tenant_slug)
    tenant = await get_branding(session, tenant_id=tenant_id)
    return public_branding(tenant)


@router.get("/{tenant_slug}/{event_slug}/slots", response_model=PublicSlotsResponse)
async def list_public_slots(  # noqa: PLR0913 - FastAPI declares each query param as a parameter
    tenant_slug: TenantSlug,
    event_slug: EventSlug,
    session: SessionDep,
    window_from: Annotated[date, Query(alias="from", description="Inclusive window start (date)")],
    window_to: Annotated[date, Query(alias="to", description="Inclusive window end (date)")],
    tz: Annotated[str, Query(description="IANA display timezone, echoed back in the response")],
) -> PublicSlotsResponse:
    """When can a guest book this service?

    The window guards — a real IANA zone, an ordered range, a bounded span — are the ones ``/slots``
    already owns, imported rather than re-implemented. Two copies of "how wide a window may a caller
    ask for" is how the UNAUTHENTICATED one ends up being the generous one.
    """
    require_iana_zone_or_422(tz)
    now = _now()
    require_window_is_sane(window_from, window_to, today=now.date())

    event_type = await _resolve_event(session, tenant_slug=tenant_slug, event_slug=event_slug)
    result = await compute_slots(
        session,
        tenant_id=event_type.tenant_id,
        event_type_id=event_type.id,
        window_from=window_from,
        window_to=window_to,
        now=now,
    )
    if result is None:  # pragma: no cover - the row was resolved one statement ago
        raise _not_found()
    return PublicSlotsResponse(
        event_slug=event_type.slug,
        timezone=tz,
        availability=result.availability,
        slots=[
            SlotRead(start=slot.start.astimezone(UTC), end=slot.end.astimezone(UTC))
            for slot in result.slots
        ],
    )


@router.post(
    "/{tenant_slug}/{event_slug}/bookings",
    status_code=status.HTTP_201_CREATED,
    response_model=PublicBookingRead,
)
async def create_public_booking(
    tenant_slug: TenantSlug,
    event_slug: EventSlug,
    payload: PublicBookingCreate,
    request: Request,
    session: SessionDep,
) -> PublicBookingRead:
    """==Book a slot with no credentials.== The one write anybody on the internet may perform.

    The ORDER of the guards is the design:

    1. **the captcha, FIRST** — before the business is resolved, before a row is read, before
       anything is written. A bot that has not solved it must not be able to make this endpoint do
       database work: an unauthenticated write that queries first and verifies afterwards is a
       denial-of-service endpoint wearing a captcha;
    2. **the resolver** — ``(tenant_slug, event_slug)``, which binds the business to the session;
    3. **the booking**, through the SAME ``create_booking`` the admin and the API key already use.
       Not a parallel copy: the anti-double-booking lock, the daily cap, the withdrawn-service
       refusal, the guest tokens and the outbox all live in there — and a second implementation of
       this path would be a second copy of those rules, with the copy that eventually falls behind
       being the one that has no authentication in front of it.

    The address is stamped here and nowhere else, because here is the only place a request from a
    *guest* exists. The admin's bookings and the API key's carry none — and are therefore not capped
    by one.
    """
    verifier: TurnstileVerifier = request.app.state.turnstile
    client_ip = _client_ip(request)
    if not await verifier.verify(payload.turnstile_token, remote_ip=client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": _CAPTCHA_REQUIRED,
                "message": "The anti-abuse challenge was not completed; please try again",
            },
        )

    event_type = await _resolve_event(session, tenant_slug=tenant_slug, event_slug=event_slug)
    settings = _settings(request)
    params = BookingParams(
        event_type_id=event_type.id,
        start=payload.start,
        guest_name=payload.guest_name,
        guest_email=payload.guest_email,
        guest_timezone=payload.guest_timezone,
        guest_notes=payload.guest_notes,
        answers=payload.answers,
        locale=payload.locale or "es",
        guest_phone=payload.guest_phone,
        guest_phone_consent=payload.guest_phone_consent,
        # ==The second of the per-IP cap's three pieces.== The column exists (migration 0011) and
        # the
        # enforcement exists (``guard.enforce_ip_cap``); this is the ONE place in the product that
        # writes a value into it, because this is the one path a stranger can reach.
        source_ip=client_ip,
    )
    # ==Paid types HOLD; free types confirm on the spot.== Only the public path creates a hold — the
    # admin and the API key are trusted and confirm directly (B-05b). A hold occupies the slot while
    # the guest pays, and the arbiter confirms it when the payment lands.
    if event_type.price_cents is not None:
        return await _start_paid_booking(
            session, request=request, event_type=event_type, params=params
        )

    try:
        booking = await create_booking(
            session,
            tenant_id=event_type.tenant_id,
            params=params,
            now=_now(),
            effects=BookingEffects(
                signer=GuestTokenSigner(settings.app_secret),
                booking_base_url=_booking_base_url(request, settings),
            ),
        )
    except BookingError as exc:
        raise _public_booking_error(exc) from exc

    await session.refresh(booking)
    return PublicBookingRead.model_validate(booking)


async def _start_paid_booking(
    session: AsyncSession,
    *,
    request: Request,
    event_type: EventType,
    params: BookingParams,
) -> PublicBookingRead:
    """Open a paid hold: BYOK credential (fail-closed) → hold + EXPIRE_HOLD → COMMIT → checkout.

    ==The order is the design (§4.4).== The credential is resolved FIRST, so a business that cannot
    charge never even opens a hold (fail-closed, no orphan). The hold and its self-cancel are
    committed BEFORE the provider I/O — *persist the intent before the call* — so a failed checkout
    leaves a hold that lapses in 30 minutes, never a charge. The checkout session's idempotency key
    is the ``booking_id``, so a retry returns the same session, never a second charge.
    """
    price_cents = event_type.price_cents
    currency = event_type.currency
    if price_cents is None or currency is None:
        # pragma: no cover - the caller only enters here for a priced type; a price with no
        # currency is a misconfiguration.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "misconfigured", "message": "This service is not configured for pay"},
        )
    gateway: PaymentGateway | None = getattr(request.app.state, "payment_gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "payments_unavailable", "message": "Payments are not configured"},
        )
    fernet_keys = request.app.state.fernet_keys
    try:
        credential = await resolve_money_credential(
            session,
            tenant_id=event_type.tenant_id,
            provider=CredentialProvider.STRIPE,
            fernet_key=fernet_keys,
        )
    except MissingCredentialError as exc:
        # ==Fail-closed: no BYOK account, no charge.== Never fall back to the instance's account.
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "payment_unavailable",
                "message": "This service cannot take payment right now",
            },
        ) from exc

    try:
        booking = await create_booking(
            session,
            tenant_id=event_type.tenant_id,
            params=params,
            now=_now(),
            effects=None,
            hold_ttl=HOLD_TTL,
        )
    except BookingError as exc:
        raise _public_booking_error(exc) from exc

    assert booking.hold_expires_at is not None  # create_booking with hold_ttl always sets it
    await enqueue_expire_hold(session, booking=booking, hold_expires_at=booking.hold_expires_at)
    # Persist the hold + its self-cancel BEFORE the provider I/O. The GUC re-stamps the next
    # transaction (the listener + ContextVar), so the writes below stay bound to this business.
    await session.commit()

    checkout = await gateway.create_checkout_session(
        idempotency_key=str(booking.id),
        amount_cents=price_cents,
        currency=currency,
        expires_at=booking.hold_expires_at,
        # ==Finding 3.== The guest returns to the REAL booking page (the same base the guest
        # cancel/reschedule links are minted against), never a dead ``example.invalid``.
        return_url=_booking_base_url(request, _settings(request)),
        secrets=credential.secrets,
    )
    session.add(
        Payment(
            tenant_id=event_type.tenant_id,
            booking_id=booking.id,
            provider=CredentialProvider.STRIPE.value,
            provider_ref=checkout.provider_ref,
            status=PaymentStatus.INTENT,
            amount_cents=price_cents,
            currency=currency,
        )
    )
    await session.flush()
    await session.refresh(booking)
    return PublicBookingRead.model_validate(booking).model_copy(
        update={"checkout_url": checkout.checkout_url}
    )


def _booking_base_url(request: Request, settings: Settings) -> str:
    """The booking-page base the guest's cancel/reschedule links are minted against."""
    configured = settings.booking_base_url
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def _public_booking_error(exc: BookingError) -> HTTPException:
    """Map a domain error to HTTP: the authenticated router's table, NARROWED by exactly one arm.

    The statuses and machine codes are the ones ``api/bookings.py`` already publishes — the booking
    page localises off those codes, and a second vocabulary here would fork them. The single change
    is that the two event-type errors collapse into this module's ``not_found``, which is also what
    an unknown BUSINESS gets: the four ways to miss stay indistinguishable to a stranger.
    """
    if isinstance(exc, EventTypeNotFoundError | EventTypeInactiveError):
        return _not_found()
    return map_booking_error(exc)


__all__ = ["router"]
