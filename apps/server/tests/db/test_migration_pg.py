"""PostgreSQL-backed parity and behavioral tests for the schema (db-marked).

SQLite proves the tables and columns exist; only a real server proves the PostgreSQL-specific
pieces work — timestamptz/JSON/UUID rendering and, above all, that the partial unique index
actually enforces "one active booking per slot, cancelled bookings free the slot" (RF-04).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db import Base
from aethercal.server.db.migrate import run_migrations

pytestmark = pytest.mark.db

_SLOT_START = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
_SLOT_END = _SLOT_START + timedelta(minutes=30)


def _insert(conn: sa.Connection, table: sa.Table, **values: object) -> object:
    return conn.execute(sa.insert(table).values(**values).returning(table.c.id)).scalar_one()


def _seed_bookable_reference(conn: sa.Connection) -> dict[str, object]:
    """Insert the parent rows a booking needs and return the FK ids that scope a slot."""
    tables = Base.metadata.tables
    tenant_id = _insert(conn, tables["tenants"], slug="acme", name="Acme")
    user_id = _insert(
        conn,
        tables["users"],
        tenant_id=tenant_id,
        email="host@example.com",
        name="Host",
        timezone="UTC",
    )
    schedule_id = _insert(
        conn,
        tables["schedules"],
        tenant_id=tenant_id,
        name="Default",
        timezone="UTC",
    )
    event_type_id = _insert(
        conn,
        tables["event_types"],
        tenant_id=tenant_id,
        host_id=user_id,
        schedule_id=schedule_id,
        slug="intro",
        title="Intro",
        duration_seconds=1800,
        max_advance_seconds=2_592_000,
    )
    return {"tenant_id": tenant_id, "event_type_id": event_type_id}


def _book(conn: sa.Connection, ref: dict[str, object], guest: str) -> object:
    return _insert(
        conn,
        Base.metadata.tables["bookings"],
        start_at=_SLOT_START,
        end_at=_SLOT_END,
        guest_name=guest,
        guest_email=f"{guest}@example.com",
        guest_timezone="UTC",
        **ref,
    )


def test_structural_parity_on_postgres(clean_pg_engine: sa.Engine) -> None:
    run_migrations(clean_pg_engine)
    inspector = sa.inspect(clean_pg_engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        actual = {col["name"] for col in inspector.get_columns(name)}
        expected = {col.name for col in table.columns}
        assert actual == expected, f"column drift in {name}: {expected ^ actual}"


def test_partial_index_blocks_a_second_active_booking_of_the_same_slot(
    clean_pg_engine: sa.Engine,
) -> None:
    run_migrations(clean_pg_engine)
    with clean_pg_engine.begin() as conn:
        ref = _seed_bookable_reference(conn)
        _book(conn, ref, "alice")
    with pytest.raises(sa.exc.IntegrityError), clean_pg_engine.begin() as conn:
        _book(conn, ref, "bob")


def test_cancelling_a_booking_frees_its_slot(clean_pg_engine: sa.Engine) -> None:
    run_migrations(clean_pg_engine)
    bookings = Base.metadata.tables["bookings"]
    with clean_pg_engine.begin() as conn:
        ref = _seed_bookable_reference(conn)
        first = _book(conn, ref, "alice")
        conn.execute(
            sa.update(bookings)
            .where(bookings.c.id == first)
            .values(status=BookingStatus.CANCELLED.value)
        )
        # The cancelled row is excluded from the partial index, so the slot is bookable again.
        _book(conn, ref, "bob")
