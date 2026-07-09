"""Tenancy: the tenant root, its users (hosts/operators), and API keys."""

from __future__ import annotations

import datetime as _dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey


class Tenant(UUIDPrimaryKey, Timestamps, Base):
    """An isolated organization. Every other row is scoped to a tenant via ``tenant_id``."""

    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(sa.String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)


class User(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """A host/operator within a tenant. The MVP admin is a single user (F1-11)."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(sa.String(255))

    __table_args__ = (sa.UniqueConstraint("tenant_id", "email"),)


class ApiKey(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """An API credential (F1-17). The prefix identifies the row (and thus the tenant); the secret
    is verified against ``hashed_key``. The prefix is globally unique so lookup needs no tenant."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(16), unique=True, nullable=False)
    hashed_key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    last_used_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))


__all__ = ["ApiKey", "Tenant", "User"]
