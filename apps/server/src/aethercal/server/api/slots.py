"""Slots availability endpoint (F1-04, RF-03/RF-16/RNF-6): the read-only, tenant-scoped slot list.

``GET /slots`` answers "when can a guest book this event type?" for the authenticated tenant. It
composes the pure engines through :func:`compute_slots` and never calls an external service in the
request path beyond ``read_busy`` reading the busy cache (RNF-6: no ``service_factory`` is injected
here). Query params: ``event_type`` (the event type id), ``from`` / ``to`` (the inclusive date
window), and ``tz`` (the requested display timezone — validated as a real IANA zone and echoed back;
the slot bounds are always absolute UTC instants regardless). Errors are clean and never leak
internals (RF-16 error envelope): 404 for an unknown event type, 422 for a bad timezone or an
inverted date range.

The orchestrator wires this ``router`` onto the ``/api/v1`` aggregator at integration; the module
owns only its ``/slots`` prefix.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.slots import SlotRead, SlotsResponse
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services.slots import compute_slots

router = APIRouter(prefix="/slots", tags=["slots"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]

_INVALID_TIMEZONE = "invalid_timezone"
_INVALID_RANGE = "invalid_range"
_NOT_FOUND = "not_found"


def _unprocessable(error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": error, "message": message},
    )


def _require_iana_zone(tz: str) -> None:
    """Reject a ``tz`` that is not a real IANA zone with a clean 422."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _unprocessable(_INVALID_TIMEZONE, f"Unknown timezone: {tz!r}") from exc


def _require_ordered_window(window_from: date, window_to: date) -> None:
    """Reject an inverted date window (``from`` after ``to``) with a clean 422."""
    if window_from > window_to:
        raise _unprocessable(_INVALID_RANGE, "'from' must not be after 'to'")


@router.get("/", response_model=SlotsResponse)
async def list_slots(  # noqa: PLR0913 — FastAPI declares each query param + dependency as a parameter
    session: SessionDep,
    ctx: AuthDep,
    event_type: Annotated[uuid.UUID, Query(description="Event type id to compute slots for")],
    window_from: Annotated[date, Query(alias="from", description="Inclusive window start (date)")],
    window_to: Annotated[date, Query(alias="to", description="Inclusive window end (date)")],
    tz: Annotated[str, Query(description="IANA display timezone, echoed back in the response")],
) -> SlotsResponse:
    """Bookable slots for one of the tenant's event types (404 unknown, 422 bad tz / range)."""
    _require_iana_zone(tz)
    _require_ordered_window(window_from, window_to)

    result = await compute_slots(
        session,
        tenant_id=ctx.tenant_id,
        event_type_id=event_type,
        window_from=window_from,
        window_to=window_to,
        now=datetime.now(UTC),
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


__all__ = ["router"]
