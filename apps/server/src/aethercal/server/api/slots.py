"""Slots availability endpoint (F1-04, RF-03/RF-16/RNF-6): the read-only, tenant-scoped slot list.

``GET /slots`` answers "when can a guest book this event type?" for the authenticated tenant. It
composes the pure engines through :func:`compute_slots` and never calls an external service in the
request path beyond ``read_busy`` reading the busy cache (RNF-6: no ``service_factory`` is injected
here). Query params: ``event_type`` (the event type id), ``from`` / ``to`` (the inclusive date
window), and ``tz`` (the requested display timezone — validated as a real IANA zone and echoed back;
the slot bounds are always absolute UTC instants regardless). Errors are clean and never leak
internals (RF-16 error envelope): 404 for an unknown event type, 422 for a bad timezone, an
inverted date range, or a window wider than ``MAX_QUERY_DAYS``.

The orchestrator wires this ``router`` onto the ``/api/v1`` aggregator at integration; the module
owns only its ``/slots`` prefix.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.bookings import require_iana_zone
from aethercal.schemas.slots import SlotRead, SlotsResponse
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.db.guc import bind_tenant
from aethercal.server.deps import get_session
from aethercal.server.services.guest_tokens import (
    GuestTokenPurpose,
    GuestTokenSigner,
    hash_token,
    verify_guest_token,
)
from aethercal.server.services.slots import compute_slots
from aethercal.server.services.tenant_resolution import tenant_by_guest_token_hash
from aethercal.server.settings import Settings

router = APIRouter(prefix="/slots", tags=["slots"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]

_INVALID_TIMEZONE = "invalid_timezone"
_INVALID_RANGE = "invalid_range"
_WINDOW_TOO_LARGE = "window_too_large"
_OUT_OF_RANGE = "window_out_of_range"
_NOT_FOUND = "not_found"

# The absolute band, relative to "now", a request may ask about. Availability is inherently a
# near-future question, so a query about year 1 or year 9999 is meaningless — and rejecting it up
# front keeps every downstream date computation (the ±1-day busy padding and the wall-time->instant
# timezone conversion) clear of date.min/date.max, where it would otherwise overflow. Generous
# enough for any real booking horizon, and nowhere near the representable extremes.
MAX_PAST_DAYS = 1
MAX_FUTURE_DAYS = 366 * 5

# The widest ``from``..``to`` a single request may span. A booking calendar rarely needs more than a
# couple of months of look-ahead at once; bounding it keeps the availability computation O(window)
# — per-day interval expansion plus a busy-set scan — instead of letting one request materialize an
# unbounded range (a cheap DoS guard, and a backstop against the extreme-date overflow path).
MAX_QUERY_DAYS = 62


def _unprocessable(error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": error, "message": message},
    )


def _require_iana_zone(tz: str) -> None:
    """Reject a ``tz`` that is not a real IANA zone with a clean 422.

    What counts as a real zone is NOT decided here. It is decided once, in the domain, by
    :func:`aethercal.core.tz.require_iana_zone` (re-exported by ``schemas.bookings``, which is where
    this module imports it from) — the same rule the guest's timezone is held to on the way into a
    booking, the host's on the way into ``services/users.py``, and the visitor's on the booking
    page. This endpoint used to re-implement it (its own ``ZoneInfo(...)``, its own ``except``),
    which is how ``/slots`` and ``/bookings`` would eventually have come to disagree about what a
    zone is: not with a bang, but the day someone fixed one copy.

    So all that is left here is a TRANSLATION. The rule refuses in the currency of the schema layer
    (a ``ValueError``, worded for a Pydantic field); this endpoint owes its callers the currency of
    the HTTP contract (a 422 with ``{"error", "message"}``). ==The message text is deliberately NOT
    the rule's==: it is a published API string that the booking page and the SDK read, so it is
    reproduced verbatim and pinned by ``test_slots_timezone_rule.py``. Reusing a rule is not licence
    to reword a contract.
    """
    try:
        require_iana_zone(tz)
    except ValueError as exc:
        raise _unprocessable(_INVALID_TIMEZONE, f"Unknown timezone: {tz!r}") from exc


def _require_ordered_window(window_from: date, window_to: date) -> None:
    """Reject an inverted date window (``from`` after ``to``) with a clean 422."""
    if window_from > window_to:
        raise _unprocessable(_INVALID_RANGE, "'from' must not be after 'to'")


def _require_window_within_cap(window_from: date, window_to: date) -> None:
    """Reject a window wider than ``MAX_QUERY_DAYS`` with a clean 422 (bounded work per request).

    Assumes ``window_from <= window_to`` (the caller runs ``_require_ordered_window`` first), so the
    span is non-negative.
    """
    if (window_to - window_from).days > MAX_QUERY_DAYS:
        raise _unprocessable(
            _WINDOW_TOO_LARGE, f"Date window must not exceed {MAX_QUERY_DAYS} days"
        )


def _require_window_in_bounds(window_from: date, window_to: date, today: date) -> None:
    """Reject a window whose dates fall outside a sane near-future band around ``today`` (422).

    Availability is a near-future question; bounding the absolute dates keeps all downstream date
    math clear of ``date.min`` / ``date.max``, where the busy padding and the timezone conversion
    would overflow even for a within-cap window (e.g. ``date.max - 1 .. date.max``).
    """
    floor = today - timedelta(days=MAX_PAST_DAYS)
    ceiling = today + timedelta(days=MAX_FUTURE_DAYS)
    if window_from < floor or window_to > ceiling:
        raise _unprocessable(
            _OUT_OF_RANGE,
            f"Date window must fall within {floor.isoformat()}..{ceiling.isoformat()}",
        )


# --------------------------------------------------------------------------------------
# The two seams the PUBLIC router consumes. ==Shared, not copied.==
#
# ``/public/{tenant_slug}/{event_slug}/slots`` asks exactly the same question of exactly the same
# engine, so it is held to exactly the same guards: a real IANA zone, an ordered range, a span
# within
# ``MAX_QUERY_DAYS``, and dates inside the sane near-future band. Two copies of "how wide a window
# may a caller ask for" is how the UNAUTHENTICATED one ends up being the generous one — and the
# generous one is the one anybody can call.
#
# The private helpers keep their names (their own tests import them); these are the doors.
# --------------------------------------------------------------------------------------


async def _authorize_read(
    request: Request, session: AsyncSession, *, token: str | None
) -> uuid.UUID:
    """Authorize a slots read by a guest's RESCHEDULE token, or by the tenant's API key.

    The same two-door shape ``api/bookings.py`` already uses for cancel/reschedule (RF-09), and the
    same forced order — resolve the business from the token's hash through the ``SECURITY DEFINER``
    resolver, BIND it, and only then read anything scoped. ``guest_tokens`` is itself a
    tenant-scoped
    table: read it under RLS with no business bound and it returns zero rows, and every link in
    every
    guest's inbox stops working.

    ==VERIFIED, never CONSUMED.== ``consume_guest_token`` stamps ``used_at``, and a token spent on a
    page render is a guest who may look at the available times exactly once and then never actually
    reschedule.
    """
    if token is None:
        ctx = await require_api_key(request, session)
        return ctx.tenant_id

    tenant_id = await tenant_by_guest_token_hash(session, hash_token(token))
    if tenant_id is None:
        raise _forbidden()
    await bind_tenant(session, tenant_id)

    signer = GuestTokenSigner(_settings(request).app_secret)
    row = await verify_guest_token(
        session, signer, token, expected_purpose=GuestTokenPurpose.RESCHEDULE
    )
    if row is None:
        raise _forbidden()
    return row.tenant_id


def _forbidden() -> HTTPException:
    """One answer for every bad link — expired, used, tampered, wrong purpose (RF-09: no oracle)."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "forbidden", "message": "Invalid or expired link"},
    )


def _settings(request: Request) -> Settings:
    value: Settings = request.app.state.settings
    return value


def require_iana_zone_or_422(tz: str) -> None:
    """Reject a ``tz`` that is not a real IANA zone, with the API's published 422."""
    _require_iana_zone(tz)


def require_window_is_sane(window_from: date, window_to: date, *, today: date) -> None:
    """Reject an inverted, over-wide or absurdly-dated window, with the API's published 422s."""
    _require_ordered_window(window_from, window_to)
    _require_window_within_cap(window_from, window_to)
    _require_window_in_bounds(window_from, window_to, today)


@router.get("/", response_model=SlotsResponse)
async def list_slots(  # noqa: PLR0913 — FastAPI declares each query param + dependency as a parameter
    request: Request,
    session: SessionDep,
    event_type: Annotated[uuid.UUID, Query(description="Event type id to compute slots for")],
    window_from: Annotated[date, Query(alias="from", description="Inclusive window start (date)")],
    window_to: Annotated[date, Query(alias="to", description="Inclusive window end (date)")],
    tz: Annotated[str, Query(description="IANA display timezone, echoed back in the response")],
    token: Annotated[
        str | None, Query(description="Signed guest token (a reschedule link from an e-mail)")
    ] = None,
) -> SlotsResponse:
    """Bookable slots for one of the tenant's event types (404 unknown, 422 bad tz / range).

    ==Authenticated by the tenant's API KEY, **or** by a guest's signed RESCHEDULE token.== That
    second door is new, and it is not a convenience — it is what keeps RF-09 alive on the day the
    booking page loses its key.

    The reschedule link already sitting in a guest's inbox carries a token, a booking id and an
    event
    type ID, and nothing else — no business, no slug. To render the picker, the page must ask this
    endpoint for that event type's slots. With no API key left in the page, and this route
    key-only, ==every reschedule link ever e-mailed would have stopped rendering its times== — while
    the cancel and reschedule POSTs, which already accept a token, kept working. A half-broken flow,
    reachable only from a real customer's inbox, and invisible to every test that speaks in slugs.

    The token is VERIFIED, never CONSUMED: it is single-use, and spending it on a page render would
    mean the guest could look at the times exactly once and never actually reschedule. It also leaks
    nothing new — the same slots are readable, with no credentials at all, from
    ``/public/{tenant_slug}/{event_slug}/slots``.
    """
    _require_iana_zone(tz)
    _require_ordered_window(window_from, window_to)
    _require_window_within_cap(window_from, window_to)
    now = datetime.now(UTC)
    _require_window_in_bounds(window_from, window_to, now.date())

    tenant_id = await _authorize_read(request, session, token=token)
    result = await compute_slots(
        session,
        tenant_id=tenant_id,
        event_type_id=event_type,
        window_from=window_from,
        window_to=window_to,
        now=now,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": _NOT_FOUND, "message": "Event type not found"},
        )
    return SlotsResponse(
        event_type_id=event_type,
        timezone=tz,
        availability=result.availability,
        slots=[
            SlotRead(start=slot.start.astimezone(UTC), end=slot.end.astimezone(UTC))
            for slot in result.slots
        ],
    )


__all__ = ["require_iana_zone_or_422", "require_window_is_sane", "router"]
