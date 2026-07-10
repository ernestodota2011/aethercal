"""Schedules + Date Overrides API (RF-15): tenant-scoped CRUD over ``/api/v1/schedules``.

Every route authenticates with an API key and is scoped to ``ctx.tenant_id`` — a caller can only
ever see or touch its own tenant's schedules. The handlers stay thin: they delegate to
``services.schedules`` and translate its domain errors into clean HTTP status codes
(404 not found, 409 duplicate, 422 invalid timezone / overlapping ranges). All calendar validation
lives in ``aethercal.core`` via the service, never here.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.schedules import (
    DateOverrideCreate,
    DateOverrideRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services.schedules import (
    DateOverrideNotFoundError,
    DuplicateDateOverrideError,
    DuplicateScheduleNameError,
    ScheduleNotFoundError,
    ScheduleServiceError,
    ScheduleValidationError,
    add_date_override,
    create_schedule,
    delete_date_override,
    delete_schedule,
    get_schedule,
    list_date_overrides,
    list_schedules,
    override_to_read,
    schedule_to_read,
    update_schedule,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])

CtxDep = Annotated[AuthContext, Depends(require_api_key)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@contextmanager
def _translate_errors() -> Generator[None]:
    """Map service errors to HTTP status codes (404 / 409 / 422)."""
    try:
        yield
    except (ScheduleNotFoundError, DateOverrideNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DuplicateScheduleNameError, DuplicateDateOverrideError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScheduleServiceError as exc:  # defensive: any future service error → 400, not a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------
# Schedules.
# --------------------------------------------------------------------------------------
@router.post("/", status_code=201)
async def create_schedule_route(
    data: ScheduleCreate, ctx: CtxDep, session: SessionDep
) -> ScheduleRead:
    """Create a weekly schedule for the caller's tenant."""
    with _translate_errors():
        row = await create_schedule(session, tenant_id=ctx.tenant_id, data=data)
    return schedule_to_read(row)


@router.get("/")
async def list_schedules_route(ctx: CtxDep, session: SessionDep) -> list[ScheduleRead]:
    """List the caller tenant's schedules."""
    rows = await list_schedules(session, tenant_id=ctx.tenant_id)
    return [schedule_to_read(row) for row in rows]


@router.get("/{schedule_id}")
async def get_schedule_route(
    ctx: CtxDep, session: SessionDep, schedule_id: Annotated[uuid.UUID, Path()]
) -> ScheduleRead:
    """Fetch one schedule owned by the caller's tenant."""
    with _translate_errors():
        row = await get_schedule(session, tenant_id=ctx.tenant_id, schedule_id=schedule_id)
    return schedule_to_read(row)


@router.patch("/{schedule_id}")
async def update_schedule_route(
    data: ScheduleUpdate,
    ctx: CtxDep,
    session: SessionDep,
    schedule_id: Annotated[uuid.UUID, Path()],
) -> ScheduleRead:
    """Patch a schedule owned by the caller's tenant."""
    with _translate_errors():
        row = await update_schedule(
            session, tenant_id=ctx.tenant_id, schedule_id=schedule_id, data=data
        )
    return schedule_to_read(row)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule_route(
    ctx: CtxDep, session: SessionDep, schedule_id: Annotated[uuid.UUID, Path()]
) -> None:
    """Delete a schedule owned by the caller's tenant."""
    with _translate_errors():
        await delete_schedule(session, tenant_id=ctx.tenant_id, schedule_id=schedule_id)


# --------------------------------------------------------------------------------------
# Date overrides (nested under a schedule; deletion is by override id).
# --------------------------------------------------------------------------------------
@router.post("/{schedule_id}/date-overrides", status_code=201)
async def add_date_override_route(
    data: DateOverrideCreate,
    ctx: CtxDep,
    session: SessionDep,
    schedule_id: Annotated[uuid.UUID, Path()],
) -> DateOverrideRead:
    """Add a per-date override to a schedule owned by the caller's tenant."""
    with _translate_errors():
        row = await add_date_override(
            session, tenant_id=ctx.tenant_id, schedule_id=schedule_id, data=data
        )
    return override_to_read(row)


@router.get("/{schedule_id}/date-overrides")
async def list_date_overrides_route(
    ctx: CtxDep, session: SessionDep, schedule_id: Annotated[uuid.UUID, Path()]
) -> list[DateOverrideRead]:
    """List the date overrides of a schedule owned by the caller's tenant."""
    with _translate_errors():
        rows = await list_date_overrides(session, tenant_id=ctx.tenant_id, schedule_id=schedule_id)
    return [override_to_read(row) for row in rows]


@router.delete("/date-overrides/{override_id}", status_code=204)
async def delete_date_override_route(
    ctx: CtxDep, session: SessionDep, override_id: Annotated[uuid.UUID, Path()]
) -> None:
    """Delete a date override owned by the caller's tenant."""
    with _translate_errors():
        await delete_date_override(session, tenant_id=ctx.tenant_id, override_id=override_id)
