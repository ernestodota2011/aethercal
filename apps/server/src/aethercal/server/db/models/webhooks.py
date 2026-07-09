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

    __table_args__ = (sa.Index("ix_webhook_deliveries_due", "status", "next_retry_at"),)


__all__ = ["Webhook", "WebhookDelivery"]
