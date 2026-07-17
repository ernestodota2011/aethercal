"""AetherCal persistence layer: SQLAlchemy 2.0 models, engines, config, and the boot migrator.

Importing this package registers every MVP table on ``Base.metadata`` (via ``.models``), so callers
can rely on ``Base.metadata`` being complete.
"""

from __future__ import annotations

from aethercal.server.db.base import Base
from aethercal.server.db.config import (
    DATABASE_URL_ENV,
    DatabaseConfig,
    normalize_database_url,
)
from aethercal.server.db.engine import (
    build_async_engine,
    build_sessionmaker,
    build_sync_engine,
)
from aethercal.server.db.migrate import make_alembic_config, run_migrations
from aethercal.server.db.models import (
    ApiKey,
    Booking,
    BusyCache,
    DateOverride,
    EventType,
    ExternalCalendarLink,
    ExternalConnection,
    GuestToken,
    Membership,
    Schedule,
    SentNotification,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)

__all__ = [
    "DATABASE_URL_ENV",
    "ApiKey",
    "Base",
    "Booking",
    "BusyCache",
    "DatabaseConfig",
    "DateOverride",
    "EventType",
    "ExternalCalendarLink",
    "ExternalConnection",
    "GuestToken",
    "Membership",
    "Schedule",
    "SentNotification",
    "Tenant",
    "User",
    "Webhook",
    "WebhookDelivery",
    "build_async_engine",
    "build_sessionmaker",
    "build_sync_engine",
    "make_alembic_config",
    "normalize_database_url",
    "run_migrations",
]
