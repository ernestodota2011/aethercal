"""Booking endpoints (F1-05, RF-04/RF-07/RF-09/RF-16): the booking lifecycle over HTTP.

``POST /bookings`` books a slot; ``GET /bookings`` / ``GET /bookings/{id}`` read them; and
``POST /bookings/{id}/cancel`` / ``POST /bookings/{id}/reschedule`` mutate one — each accepting
EITHER the tenant's API key OR a signed guest token (``?token=``) so the public booking page and the
email links can self-serve without an account (RF-09). The handlers stay thin: they translate the
service's domain errors into the clean HTTP envelope (409 slot taken, 404 unknown, 503 availability
degraded) and let ``get_session`` own the transaction.

The ``effects`` bundle is assembled from ``app.state`` (the guest-token signer from ``app_secret``);
an effect whose runtime config is absent — no SMTP sender, no reminder runner, no Google connection
— degrades gracefully (the booking still succeeds, the effect is skipped) and never 500s. The
orchestrator wires this ``router`` onto the ``/api/v1`` aggregator at integration; the module owns
only its ``/bookings`` prefix.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingCreate, BookingRead, BookingReschedule
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services.bookings import (
    AvailabilityUnavailableError,
    BookingEffects,
    BookingError,
    BookingNotActiveError,
    BookingNotFoundError,
    BookingParams,
    EventTypeNotFoundError,
    SlotUnavailableError,
    cancel_booking,
    create_booking,
    get_booking,
    list_bookings,
    reschedule_booking,
)
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    consume_guest_token,
)
from aethercal.server.settings import Settings

router = APIRouter(prefix="/bookings", tags=["bookings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]
TokenQuery = Annotated[
    str | None, Query(description="Signed guest token (email/booking-page link)")
]


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Error mapping (RF-16 envelope: a machine ``error`` code + a safe ``message``).
# --------------------------------------------------------------------------------------


def _http(code: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"error": error, "message": message})


def _map_booking_error(exc: BookingError) -> HTTPException:
    """Translate a service domain error to its clean HTTP status (fixed, non-leaking messages)."""
    match exc:
        case EventTypeNotFoundError():
            return _http(status.HTTP_404_NOT_FOUND, "not_found", "Event type not found")
        case BookingNotFoundError():
            return _http(status.HTTP_404_NOT_FOUND, "not_found", "Booking not found")
        case AvailabilityUnavailableError():
            return _http(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "availability_unavailable",
                "Host availability is temporarily unavailable; please try again",
            )
        case BookingNotActiveError():
            return _http(status.HTTP_409_CONFLICT, "not_active", "Booking cannot be rescheduled")
        case SlotUnavailableError():
            return _http(
                status.HTTP_409_CONFLICT, "slot_unavailable", "That time is no longer available"
            )
        case _:  # pragma: no cover - defensive; every BookingError subclass is handled above
            return _http(status.HTTP_409_CONFLICT, "conflict", "Booking could not be completed")


# --------------------------------------------------------------------------------------
# Effects bundle + guest-token-or-API-key authorization for the public mutation routes.
# --------------------------------------------------------------------------------------


def _settings(request: Request) -> Settings:
    value: Settings = request.app.state.settings
    return value


def _booking_base_url(request: Request, settings: Settings) -> str:
    """The public booking-page base for guest links: a configured setting, else the request base."""
    configured = getattr(settings, "booking_base_url", None)
    if isinstance(configured, str) and configured:
        return configured
    return str(request.base_url).rstrip("/")


def _build_effects(request: Request) -> BookingEffects:
    """Assemble the side-effects bundle from ``app.state`` (all effects degrade gracefully).

    The guest-token signer + booking base URL are always present. The SMTP sender and reminder
    runner are read from ``app.state`` when the integrator has wired them (F1-08/F1-10); until then
    they are ``None`` and those effects are skipped. Request-path Google sync (F1-07) is not wired
    here (it needs a per-host live client), so ``connection``/``google_service`` stay ``None`` — a
    booking never attempts Google in this path and never 500s on its absence.
    """
    settings = _settings(request)
    return BookingEffects(
        signer=GuestTokenSigner(settings.app_secret),
        booking_base_url=_booking_base_url(request, settings),
        sender=getattr(request.app.state, "email_sender", None),
        reminder_runner=getattr(request.app.state, "reminder_runner", None),
    )


async def _authorize_mutation(
    request: Request,
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    token: str | None,
    purpose: GuestTokenPurpose,
) -> uuid.UUID:
    """Authorize a cancel/reschedule by a guest token OR the API key; return the tenant id.

    A present ``token`` is consumed for ``purpose`` and must bind to THIS booking; any failure
    (bad/expired/used/wrong-purpose/mismatched token) collapses to a generic 403 that leaks no
    booking data (RF-09). With no token, the API key is required (a missing/invalid key raises the
    app-wide 401). Consuming the token in the request transaction means a later failed mutation
    (rolled back by ``get_session``) also rolls back the token's single-use stamp — the guest can
    retry.
    """
    if token is not None:
        signer = GuestTokenSigner(_settings(request).app_secret)
        row = await consume_guest_token(session, signer, token, expected_purpose=purpose)
        if row is None or row.booking_id != booking_id:
            raise _http(status.HTTP_403_FORBIDDEN, "forbidden", "Invalid or expired link")
        return row.tenant_id
    ctx = await require_api_key(request, session)
    return ctx.tenant_id


async def _read_model(session: AsyncSession, booking: object) -> BookingRead:
    """Materialize a freshly-written booking's server defaults, then serialize it (RF-16)."""
    await session.refresh(booking)
    return BookingRead.model_validate(booking)


# --------------------------------------------------------------------------------------
# Routes.
# --------------------------------------------------------------------------------------


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=BookingRead)
async def create(
    payload: BookingCreate, request: Request, session: SessionDep, ctx: AuthDep
) -> BookingRead:
    """Book a slot for the tenant (201; 409 taken/unavailable, 404 unknown, 503 degraded)."""
    params = BookingParams(
        event_type_id=payload.event_type_id,
        start=payload.start,
        guest_name=payload.guest_name,
        guest_email=payload.guest_email,
        guest_timezone=payload.guest_timezone,
        guest_notes=payload.guest_notes,
        answers=payload.answers,
        locale=payload.locale or "es",
    )
    try:
        booking = await create_booking(
            session,
            tenant_id=ctx.tenant_id,
            params=params,
            now=_now(),
            effects=_build_effects(request),
        )
    except BookingError as exc:
        raise _map_booking_error(exc) from exc
    return await _read_model(session, booking)


@router.get("/", response_model=list[BookingRead])
async def list_all(
    session: SessionDep,
    ctx: AuthDep,
    status_filter: Annotated[BookingStatus | None, Query(alias="status")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> list[BookingRead]:
    """List the tenant's bookings, filtered by ``status`` and a ``from``/``to`` date window."""
    rows = await list_bookings(
        session,
        tenant_id=ctx.tenant_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
    )
    return [BookingRead.model_validate(row) for row in rows]


@router.get("/{booking_id}", response_model=BookingRead)
async def retrieve(booking_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> BookingRead:
    """Fetch one of the tenant's bookings by id (404 if absent)."""
    booking = await get_booking(session, tenant_id=ctx.tenant_id, booking_id=booking_id)
    if booking is None:
        raise _http(status.HTTP_404_NOT_FOUND, "not_found", "Booking not found")
    return BookingRead.model_validate(booking)


@router.post("/{booking_id}/cancel", response_model=BookingRead)
async def cancel(
    booking_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    token: TokenQuery = None,
) -> BookingRead:
    """Cancel a booking via API key or a signed guest token (RF-09). Idempotent; 404 if absent."""
    tenant_id = await _authorize_mutation(
        request, session, booking_id=booking_id, token=token, purpose=GuestTokenPurpose.CANCEL
    )
    try:
        booking = await cancel_booking(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            now=_now(),
            effects=_build_effects(request),
        )
    except BookingError as exc:
        raise _map_booking_error(exc) from exc
    return await _read_model(session, booking)


@router.post("/{booking_id}/reschedule", response_model=BookingRead)
async def reschedule(
    booking_id: uuid.UUID,
    payload: BookingReschedule,
    request: Request,
    session: SessionDep,
    token: TokenQuery = None,
) -> BookingRead:
    """Reschedule via API key or a signed guest token (RF-09); 409 if the new slot is taken."""
    tenant_id = await _authorize_mutation(
        request, session, booking_id=booking_id, token=token, purpose=GuestTokenPurpose.RESCHEDULE
    )
    try:
        booking = await reschedule_booking(
            session,
            tenant_id=tenant_id,
            booking_id=booking_id,
            new_start=payload.new_start,
            now=_now(),
            effects=_build_effects(request),
        )
    except BookingError as exc:
        raise _map_booking_error(exc) from exc
    return await _read_model(session, booking)


__all__ = ["router"]
