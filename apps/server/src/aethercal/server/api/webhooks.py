"""Webhook subscription CRUD endpoints (RF-17).

All routes are tenant-scoped through ``require_api_key`` (``ctx.tenant_id``). The per-subscriber
secret is returned exactly once — in the ``POST`` response (:class:`WebhookCreated`); every other
response is a :class:`WebhookRead`, which never carries it.

The router exposes a module-level ``router`` an integrator mounts (F1-08); it is deliberately not
pre-added to ``api_router`` so waves never collide in ``api/__init__.py``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aethercal.schemas.webhooks import (
    WebhookCreate,
    WebhookCreated,
    WebhookRead,
    WebhookUpdate,
)
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services import webhooks as service
from aethercal.server.settings import Settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_AuthCtx = Annotated[AuthContext, Depends(require_api_key)]
_Session = Annotated[AsyncSession, Depends(get_session)]


def _fernet_key(request: Request) -> bytes:
    """The app Fernet key used to encrypt subscriber secrets at rest."""
    settings: Settings = request.app.state.settings
    return settings.fernet_key()


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    request: Request, body: WebhookCreate, ctx: _AuthCtx, session: _Session
) -> WebhookCreated:
    """Subscribe a new webhook. This response is the ONLY place the secret is returned."""
    webhook, secret = await service.create_webhook(
        session, tenant_id=ctx.tenant_id, params=body, fernet_key=_fernet_key(request)
    )
    # Build from the materialized columns (never the stored, encrypted ``secret``).
    return WebhookCreated(
        id=webhook.id,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=secret,
    )


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(ctx: _AuthCtx, session: _Session) -> list[WebhookRead]:
    """List every subscription owned by the caller's tenant."""
    webhooks = await service.list_webhooks(session, tenant_id=ctx.tenant_id)
    return [WebhookRead.model_validate(webhook) for webhook in webhooks]


@router.get("/{webhook_id}", response_model=WebhookRead)
async def get_webhook(webhook_id: uuid.UUID, ctx: _AuthCtx, session: _Session) -> WebhookRead:
    """Read one subscription, or 404 if it does not belong to the tenant."""
    webhook = await service.get_webhook(session, tenant_id=ctx.tenant_id, webhook_id=webhook_id)
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return WebhookRead.model_validate(webhook)


@router.patch("/{webhook_id}", response_model=WebhookRead)
async def update_webhook(
    webhook_id: uuid.UUID, body: WebhookUpdate, ctx: _AuthCtx, session: _Session
) -> WebhookRead:
    """Toggle ``active`` / change ``url`` or ``events``. 404 if not the tenant's."""
    webhook = await service.update_webhook(
        session, tenant_id=ctx.tenant_id, webhook_id=webhook_id, changes=body
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return WebhookRead.model_validate(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: uuid.UUID, ctx: _AuthCtx, session: _Session) -> Response:
    """Delete one subscription. 204 on success, 404 if not the tenant's."""
    deleted = await service.delete_webhook(session, tenant_id=ctx.tenant_id, webhook_id=webhook_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
