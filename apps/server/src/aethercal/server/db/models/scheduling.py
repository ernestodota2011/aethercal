"""Scheduling config: reusable weekly Schedules, per-date overrides, and bookable EventTypes.

Availability windows are stored as JSON that maps directly onto the pure ``aethercal.core`` value
objects (``Schedule.by_weekday`` / ``DateOverride.ranges``): the core owns the date math, and a
service loads the whole aggregate and hands it over — so the windows never need to be queried in
SQL, and this stays a small handful of tables instead of one row per time range."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class Schedule(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A reusable weekly availability pattern (RF-15). ``rules`` maps weekday to open ranges."""

    __tablename__ = "schedules"

    # NULL = a BUSINESS-WIDE schedule (the shop's opening hours), shared by every host. A concrete
    # id makes it that one host's personal availability. REQUIRED by RF-30: without it `schedules`
    # is only tenant-scoped, so two hosts silently share one schedule and there is no way to say
    # otherwise. Nullable because business-wide is the common case for the non-technical businesses
    # this product serves — a salon has opening hours, not a calendar per stylist.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict, nullable=False)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "name"),)


class DateOverride(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """A per-date exception replacing the weekly schedule (RF-15); empty ranges closes the day."""

    __tablename__ = "date_overrides"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[_dt.date] = mapped_column(sa.Date, nullable=False)
    ranges: Mapped[list[Any]] = mapped_column(sa.JSON, default=list, nullable=False)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "schedule_id", "date"),)


class EventType(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A bookable meeting type (RF-14): duration, spacing, booking window, and form questions."""

    __tablename__ = "event_types"

    host_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(sa.String(63), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    # ``title``/``description`` above are the canonical fallback (the tenant's base-locale text).
    # These hold only sparse per-locale overrides, e.g. ``{"en": "Discovery call"}`` — a locale with
    # no entry here falls back to the canonical text. Empty dict = no translations yet; no backfill
    # needed for existing rows.
    title_translations: Mapped[dict[str, str]] = mapped_column(
        sa.JSON, default=dict, nullable=False
    )
    description_translations: Mapped[dict[str, str]] = mapped_column(
        sa.JSON, default=dict, nullable=False
    )
    location: Mapped[str | None] = mapped_column(sa.String(255))
    duration_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    buffer_before_seconds: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    buffer_after_seconds: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    min_notice_seconds: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    max_advance_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    increment_seconds: Mapped[int | None] = mapped_column(sa.Integer)
    max_per_day: Mapped[int | None] = mapped_column(sa.Integer)
    questions: Mapped[list[Any]] = mapped_column(sa.JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"), nullable=False)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "slug"),)


__all__ = ["DateOverride", "EventType", "Schedule"]
