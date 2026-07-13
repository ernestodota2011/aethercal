"""Outbound webhook subscriptions and their delivery log (RF-17)."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class Webhook(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A subscriber that receives HMAC-signed events. ``secret`` is stored encrypted."""

    __tablename__ = "webhooks"

    url: Mapped[str] = mapped_column(sa.String(2048), nullable=False)
    secret: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    events: Mapped[list[Any]] = mapped_column(sa.JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"), nullable=False)


class WebhookDelivery(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """One delivery attempt record, driving exponential retries (RF-17)."""

    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), server_default=sa.text("'pending'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"), nullable=False)
    last_attempt_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    next_retry_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    response_code: Mapped[int | None] = mapped_column(sa.Integer)
    error_reason: Mapped[str | None] = mapped_column(sa.String(32))
    """WHY the last attempt did not succeed — one of
    :data:`~aethercal.server.webhooks.delivery.DELIVERY_FAILURE_REASONS`, or ``NULL`` on success (a
    recovered row clears it, so the column never keeps a stale reason).

    ==Without it, ``dead`` was the answer to four different questions.== An SSRF attempt, a
    self-hoster's own LAN address with no allowlist declared, a DNS blip, and a subscriber that
    5xx'd six times all produced the same row — ``dead``, ``response_code = NULL``, nothing else —
    so an operator whose n8n was receiving nothing had no way to tell which had happened. Persisted
    rather than merely logged or counted: the operator reads the TABLE, a process counter resets on
    restart, and a log line is gone by the time anybody asks."""

    __table_args__ = (sa.Index("ix_webhook_deliveries_due", "status", "next_retry_at"),)


__all__ = ["Webhook", "WebhookDelivery"]
