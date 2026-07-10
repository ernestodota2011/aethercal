"""EventType CRUD endpoints (RF-14).

A tenant's bookable meeting types, all scoped to the authenticated tenant (``require_api_key`` →
``AuthContext.tenant_id``). The handlers stay thin: they translate the service layer's domain
errors into clean HTTP — a duplicate slug is ``409``, a bad host/schedule ref is ``422``, and an
absent row is ``404`` — and let the ``get_session`` dependency own the transaction.

The orchestrator wires this ``router`` onto the ``/api/v1`` aggregator at integration time; the
module only owns its own ``/event-types`` prefix.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.event_types import EventTypeCreate, EventTypeRead, EventTypeUpdate
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services.event_types import (
    DuplicateSlugError,
    InvalidReferenceError,
    create_event_type,
    deactivate_event_type,
    get_event_type,
    list_event_types,
    update_event_type,
)

router = APIRouter(prefix="/event-types", tags=["event-types"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]

_DUPLICATE_SLUG = "duplicate_slug"
_INVALID_REFERENCE = "invalid_reference"
_NOT_FOUND = "not_found"


def _duplicate_slug(exc: DuplicateSlugError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": _DUPLICATE_SLUG, "message": str(exc)},
    )


def _invalid_reference(exc: InvalidReferenceError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": _INVALID_REFERENCE, "message": str(exc)},
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": _NOT_FOUND, "message": "Event type not found"},
    )


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=EventTypeRead)
async def create(payload: EventTypeCreate, session: SessionDep, ctx: AuthDep) -> EventTypeRead:
    """Create an event type for the authenticated tenant (409 duplicate slug, 422 bad reference)."""
    try:
        row = await create_event_type(session, tenant_id=ctx.tenant_id, data=payload)
    except DuplicateSlugError as exc:
        raise _duplicate_slug(exc) from exc
    except InvalidReferenceError as exc:
        raise _invalid_reference(exc) from exc
    return EventTypeRead.model_validate(row)


@router.get("/", response_model=list[EventTypeRead])
async def list_all(session: SessionDep, ctx: AuthDep) -> list[EventTypeRead]:
    """List the tenant's event types (active and inactive)."""
    rows = await list_event_types(session, tenant_id=ctx.tenant_id)
    return [EventTypeRead.model_validate(row) for row in rows]


@router.get("/{event_type_id}", response_model=EventTypeRead)
async def retrieve(event_type_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> EventTypeRead:
    """Fetch one of the tenant's event types by id (404 if absent)."""
    row = await get_event_type(session, tenant_id=ctx.tenant_id, event_type_id=event_type_id)
    if row is None:
        raise _not_found()
    return EventTypeRead.model_validate(row)


@router.patch("/{event_type_id}", response_model=EventTypeRead)
async def patch(
    event_type_id: uuid.UUID,
    payload: EventTypeUpdate,
    session: SessionDep,
    ctx: AuthDep,
) -> EventTypeRead:
    """Partially update one of the tenant's event types (404/409/422 as applicable)."""
    try:
        row = await update_event_type(
            session, tenant_id=ctx.tenant_id, event_type_id=event_type_id, data=payload
        )
    except DuplicateSlugError as exc:
        raise _duplicate_slug(exc) from exc
    except InvalidReferenceError as exc:
        raise _invalid_reference(exc) from exc
    if row is None:
        raise _not_found()
    return EventTypeRead.model_validate(row)


@router.delete("/{event_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(event_type_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> Response:
    """Soft-delete (deactivate) one of the tenant's event types; 204 on success, 404 if absent."""
    ok = await deactivate_event_type(session, tenant_id=ctx.tenant_id, event_type_id=event_type_id)
    if not ok:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
