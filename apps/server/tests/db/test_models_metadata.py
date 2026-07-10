"""Structural invariants of the SQLAlchemy models — no database required.

These assert the F1-01 contract directly off ``Base.metadata``: every MVP entity exists, every
tenant-scoped table carries a non-null ``tenant_id`` foreign key to ``tenants``, the tenant-scoping
composite constraints are in place, and the anti-double-booking index exists. Metadata is built at
import time, so these run on every CI cell with no DB.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db import Base
from aethercal.server.db.migrate import run_migrations

# The MVP entity set (F1); nothing from F2+ (no Workflow, Payment, Membership/RBAC, multi-host).
# ``outbox`` (the transactional-outbox queue for booking post-commit effects) landed with the F1-05
# residual fix in migration 0003. This set is asserted exactly, so an accidental omission or a stray
# extra table fails loudly.
EXPECTED_TABLES = {
    "tenants",
    "users",
    "api_keys",
    "schedules",
    "date_overrides",
    "event_types",
    "bookings",
    "guest_tokens",
    "external_connections",
    "external_calendar_links",
    "busy_cache",
    "webhooks",
    "webhook_deliveries",
    "sent_notifications",
    "outbox",
}

# tenants is the tenant root; every other table hangs off it via tenant_id.
TENANT_ROOT = "tenants"


def _unique_column_sets(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(col.name for col in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_exactly_the_mvp_tables_are_defined() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_every_tenant_scoped_table_has_a_non_null_tenant_id_fk() -> None:
    for name, table in Base.metadata.tables.items():
        if name == TENANT_ROOT:
            assert "tenant_id" not in table.c, "the tenant root must not carry its own tenant_id"
            continue
        assert "tenant_id" in table.c, f"{name} is missing tenant_id"
        column = table.c["tenant_id"]
        assert not column.nullable, f"{name}.tenant_id must be NOT NULL"
        fks = list(column.foreign_keys)
        assert len(fks) == 1, f"{name}.tenant_id must have exactly one foreign key"
        assert fks[0].column.table.name == TENANT_ROOT, f"{name}.tenant_id must reference tenants"


def test_every_table_has_a_primary_key() -> None:
    for name, table in Base.metadata.tables.items():
        assert list(table.primary_key.columns), f"{name} has no primary key"


def test_tenant_scoping_unique_constraints() -> None:
    assert ("tenant_id", "email") in _unique_column_sets("users")
    assert ("tenant_id", "slug") in _unique_column_sets("event_types")
    assert ("tenant_id", "name") in _unique_column_sets("schedules")
    assert ("tenant_id", "schedule_id", "date") in _unique_column_sets("date_overrides")
    assert ("tenant_id", "booking_id", "kind") in _unique_column_sets("sent_notifications")


def test_tenant_slug_is_globally_unique() -> None:
    column = Base.metadata.tables["tenants"].c["slug"]
    assert column.unique or ("slug",) in _unique_column_sets("tenants")


def test_api_key_prefix_is_globally_unique() -> None:
    # API auth extracts the prefix from the presented key, looks up the row (which identifies the
    # tenant), then verifies the hash — so the prefix must be unique across all tenants, not
    # scoped by tenant_id.
    column = Base.metadata.tables["api_keys"].c["prefix"]
    assert column.unique or ("prefix",) in _unique_column_sets("api_keys")


def test_booking_status_uses_the_core_vocabulary() -> None:
    column = Base.metadata.tables["bookings"].c["status"]
    assert isinstance(column.type, sa.Enum)
    assert set(column.type.enums) == {status.value for status in BookingStatus}


def test_anti_double_booking_partial_unique_index_exists() -> None:
    table = Base.metadata.tables["bookings"]
    by_name = {index.name: index for index in table.indexes}
    assert "uq_bookings_active_slot" in by_name, "missing the anti-double-booking index"
    index = by_name["uq_bookings_active_slot"]
    assert index.unique
    assert tuple(col.name for col in index.columns) == ("tenant_id", "event_type_id", "start_at")
    # The partial predicate (cancelled bookings free their slot) is PostgreSQL-specific.
    assert index.dialect_kwargs.get("postgresql_where") is not None


def test_naming_convention_yields_deterministic_constraint_names() -> None:
    # Deterministic names keep Alembic autogenerate drift-free across machines.
    fk = next(iter(Base.metadata.tables["users"].foreign_key_constraints))
    assert fk.name is not None
    assert fk.name.startswith("fk_users_")


def test_event_type_translation_columns_are_json_overrides_with_empty_default(
    tmp_path: Path,
) -> None:
    """web-qa-auditor NO-GO fix: the EN booking surface must not fall back to the tenant's
    base-locale ``title``/``description`` under English chrome. ``title``/``description`` stay the
    canonical fallback (the tenant's base locale); the ``*_translations`` columns hold only sparse
    locale overrides (e.g. ``{"en": "Discovery call"}``), defaulting to ``{}`` so no backfill is
    needed for existing rows.
    """
    table = Base.metadata.tables["event_types"]
    for name in ("title_translations", "description_translations"):
        column = table.c[name]
        assert isinstance(column.type, sa.JSON), f"{name} must be a JSON column"
        assert not column.nullable, f"{name} must be NOT NULL"
        assert column.default is not None and column.default.is_callable, (
            f"{name} must have a callable Python-side default"
        )
        assert column.default.arg(None) == {}, f"{name} must default to an empty dict"

    # SQLite parity: the migration must create exactly the columns the model declares (no DB
    # server required — this runs on every CI cell, same discipline as test_migration_parity.py).
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'event_type_translations.sqlite'}")
    run_migrations(engine)
    inspector = sa.inspect(engine)
    actual = {col["name"] for col in inspector.get_columns("event_types")}
    assert {"title_translations", "description_translations"} <= actual
    engine.dispose()
