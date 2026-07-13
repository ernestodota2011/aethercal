"""Workflow rule + template CRUD endpoints (RF-24).

The tenant-facing surface of the notification engine: author a rule, list them, edit one, switch it
on or off, and keep the message bodies it renders. Everything is scoped to the authenticated tenant
(``require_api_key`` → ``AuthContext.tenant_id``), and the handlers stay thin — they translate the
service's domain errors into clean HTTP (a duplicate name is ``409``, a self-defeating rule or a bad
event-type reference is ``422``, an absent row is ``404``) and let ``get_session`` own the
transaction.

Two routers, deliberately. A template belongs to the TENANT, not to one rule (several rules resolve
through the same ``(channel, kind, locale)`` body), so it is not nested under ``/workflows/{id}`` —
and a ``/workflows/templates`` path would in any case collide with ``/workflows/{workflow_id}``,
which matches ``templates``, fails to parse it as a UUID, and answers 422.

``DELETE /workflows/{id}`` DEACTIVATES, mirroring ``event_types`` (a soft delete). A rule with
steps already queued against real bookings is not something a DELETE should quietly erase;
switching it off is reversible, and the drain refuses to deliver a step whose rule is off.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.workflows import (
    WorkflowCreate,
    WorkflowRead,
    WorkflowStepRead,
    WorkflowTemplateCreate,
    WorkflowTemplateRead,
    WorkflowTemplateUpdate,
    WorkflowUpdate,
)
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.db.models import WorkflowTemplate
from aethercal.server.deps import get_session
from aethercal.server.services.workflow_rules import (
    DuplicateNameError,
    DuplicateTemplateError,
    InvalidReferenceError,
    InvalidRuleError,
    Rule,
    TemplateInUseError,
    create_template,
    create_workflow,
    delete_template,
    get_template,
    get_workflow,
    list_templates,
    list_workflows,
    set_workflow_active,
    update_template,
    update_workflow,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])
templates_router = APIRouter(prefix="/workflow-templates", tags=["workflows"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]

_DUPLICATE_NAME = "duplicate_name"
_DUPLICATE_TEMPLATE = "duplicate_template"
_INVALID_REFERENCE = "invalid_reference"
_INVALID_RULE = "invalid_rule"
_TEMPLATE_IN_USE = "template_in_use"
_NOT_FOUND = "not_found"


def _conflict(code: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"error": code, "message": str(exc)}
    )


def _unprocessable(code: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": code, "message": str(exc)},
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": _NOT_FOUND, "message": f"{what} not found"},
    )


def _now() -> datetime:
    """The instant a rule change is reconciled against. A step whose new send time has already
    passed is retired rather than fired late — the clock is part of the operation, not a
    detail."""
    return datetime.now(UTC)


def _read(rule: Rule) -> WorkflowRead:
    """A rule + its steps as the API returns it (there is no ORM relationship — see ``Rule``)."""
    workflow = rule.workflow
    return WorkflowRead(
        id=workflow.id,
        name=workflow.name,
        trigger=workflow.trigger,  # type: ignore[arg-type]  # validated in; the set is test-locked
        offset_minutes=workflow.offset_minutes,
        event_type_id=workflow.event_type_id,
        active=workflow.active,
        steps=[WorkflowStepRead.model_validate(step) for step in rule.steps],
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _read_template(row: WorkflowTemplate) -> WorkflowTemplateRead:
    return WorkflowTemplateRead.model_validate(row)


# --------------------------------------------------------------------------------------
# Rules.
# --------------------------------------------------------------------------------------


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=WorkflowRead)
async def create(payload: WorkflowCreate, session: SessionDep, ctx: AuthDep) -> WorkflowRead:
    """Author a rule (409 duplicate name, 422 bad event type / unrenderable step).

    It is ARMED on creation: the bookings the tenant already has on the books get their steps queued
    too, not only the ones taken from now on."""
    try:
        rule = await create_workflow(session, tenant_id=ctx.tenant_id, data=payload, now=_now())
    except DuplicateNameError as exc:
        raise _conflict(_DUPLICATE_NAME, exc) from exc
    except InvalidReferenceError as exc:
        raise _unprocessable(_INVALID_REFERENCE, exc) from exc
    except InvalidRuleError as exc:
        raise _unprocessable(_INVALID_RULE, exc) from exc
    return _read(rule)


@router.get("/", response_model=list[WorkflowRead])
async def list_all(session: SessionDep, ctx: AuthDep) -> list[WorkflowRead]:
    """Every rule of the tenant (active and inactive), each with its steps."""
    return [_read(rule) for rule in await list_workflows(session, tenant_id=ctx.tenant_id)]


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def retrieve(workflow_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> WorkflowRead:
    """One of the tenant's rules by id (404 if absent, or another tenant's)."""
    rule = await get_workflow(session, tenant_id=ctx.tenant_id, workflow_id=workflow_id)
    if rule is None:
        raise _not_found("Workflow")
    return _read(rule)


@router.patch("/{workflow_id}", response_model=WorkflowRead)
async def patch(
    workflow_id: uuid.UUID, payload: WorkflowUpdate, session: SessionDep, ctx: AuthDep
) -> WorkflowRead:
    """Edit a rule — and RE-TIME the steps already queued for the bookings it governs.

    ``steps``, when sent, replaces the step list wholesale (matched by channel, so a surviving step
    keeps its id, and therefore its exactly-once identity)."""
    try:
        rule = await update_workflow(
            session, tenant_id=ctx.tenant_id, workflow_id=workflow_id, data=payload, now=_now()
        )
    except DuplicateNameError as exc:
        raise _conflict(_DUPLICATE_NAME, exc) from exc
    except InvalidReferenceError as exc:
        raise _unprocessable(_INVALID_REFERENCE, exc) from exc
    except InvalidRuleError as exc:
        raise _unprocessable(_INVALID_RULE, exc) from exc
    if rule is None:
        raise _not_found("Workflow")
    return _read(rule)


@router.post("/{workflow_id}/activate", response_model=WorkflowRead)
async def activate(workflow_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> WorkflowRead:
    """Switch a rule on — and arm the bookings that were taken while it was off."""
    rule = await set_workflow_active(
        session, tenant_id=ctx.tenant_id, workflow_id=workflow_id, active=True, now=_now()
    )
    if rule is None:
        raise _not_found("Workflow")
    return _read(rule)


@router.post("/{workflow_id}/deactivate", response_model=WorkflowRead)
async def deactivate(workflow_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> WorkflowRead:
    """Switch a rule off. Its queued steps go inert — the drain refuses a step whose rule is off."""
    rule = await set_workflow_active(
        session, tenant_id=ctx.tenant_id, workflow_id=workflow_id, active=False, now=_now()
    )
    if rule is None:
        raise _not_found("Workflow")
    return _read(rule)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(workflow_id: uuid.UUID, session: SessionDep, ctx: AuthDep) -> Response:
    """Soft-delete (deactivate) a rule; 204 on success, 404 if absent. Mirrors ``event_types``."""
    rule = await set_workflow_active(
        session, tenant_id=ctx.tenant_id, workflow_id=workflow_id, active=False, now=_now()
    )
    if rule is None:
        raise _not_found("Workflow")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------------
# Templates.
# --------------------------------------------------------------------------------------


@templates_router.post(
    "/", status_code=status.HTTP_201_CREATED, response_model=WorkflowTemplateRead
)
async def create_template_endpoint(
    payload: WorkflowTemplateCreate, session: SessionDep, ctx: AuthDep
) -> WorkflowTemplateRead:
    """Store the body for one (channel, kind, locale) — 409 when that identity already exists."""
    try:
        row = await create_template(session, tenant_id=ctx.tenant_id, data=payload)
    except DuplicateTemplateError as exc:
        raise _conflict(_DUPLICATE_TEMPLATE, exc) from exc
    return _read_template(row)


@templates_router.get("/", response_model=list[WorkflowTemplateRead])
async def list_templates_endpoint(session: SessionDep, ctx: AuthDep) -> list[WorkflowTemplateRead]:
    """Every template of the tenant."""
    return [_read_template(row) for row in await list_templates(session, tenant_id=ctx.tenant_id)]


@templates_router.get("/{template_id}", response_model=WorkflowTemplateRead)
async def retrieve_template(
    template_id: uuid.UUID, session: SessionDep, ctx: AuthDep
) -> WorkflowTemplateRead:
    """One of the tenant's templates by id (404 if absent, or another tenant's)."""
    row = await get_template(session, tenant_id=ctx.tenant_id, template_id=template_id)
    if row is None:
        raise _not_found("Template")
    return _read_template(row)


@templates_router.patch("/{template_id}", response_model=WorkflowTemplateRead)
async def patch_template(
    template_id: uuid.UUID,
    payload: WorkflowTemplateUpdate,
    session: SessionDep,
    ctx: AuthDep,
) -> WorkflowTemplateRead:
    """Edit a template's text. Its (channel, kind, locale) identity is immutable by design."""
    row = await update_template(
        session, tenant_id=ctx.tenant_id, template_id=template_id, data=payload
    )
    if row is None:
        raise _not_found("Template")
    return _read_template(row)


@templates_router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template_endpoint(
    template_id: uuid.UUID, session: SessionDep, ctx: AuthDep
) -> Response:
    """Delete a template — REFUSED (409) while it is the last body a live step can render.

    Allowing it would leave that step ``active`` and mute: skipped at every send, with a reason
    nobody is reading, and the guest never messaged."""
    try:
        deleted = await delete_template(session, tenant_id=ctx.tenant_id, template_id=template_id)
    except TemplateInUseError as exc:
        raise _conflict(_TEMPLATE_IN_USE, exc) from exc
    if not deleted:
        raise _not_found("Template")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
