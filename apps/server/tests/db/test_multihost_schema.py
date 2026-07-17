"""Structural invariants of the multi-host + connected-calendar schema (RF-30, RF-11).

Three silent no-ops are closed at the schema level here, so the services above have something
truthful to stand on:

* ``schedules.user_id`` — a schedule was only tenant-scoped, so two hosts could share one weekly
  pattern **by accident** and neither the model nor the DB would say a word (RF-30).
* ``external_calendar_links.busy`` / ``.is_booking_target`` — the table existed, was written by
  nobody and read by nobody (the calendar id was the hard-coded constant ``"primary"``). The flags
  make it the operator's real configuration surface, and the partial unique index makes "two booking
  targets on one connection" a database error instead of an arbitrary pick.
* ``bookings.external_connection_id`` / ``.external_calendar_id`` — a booking recorded the id of its
  Google event but not WHERE that event lives, so a later cancel/reschedule could only guess the
  calendar. The event's home is now persisted with it.

These are metadata assertions: they need no database and run on every CI cell. The DDL that backs
them belongs to the batch's SINGLE migration (``0005``, owned serially by the foundations wave) —
the columns are declared here and CONSUMED here, never created by a second migration, so the
Alembic chain keeps exactly one head.
"""

from __future__ import annotations

import sqlalchemy as sa

from aethercal.server.db import Base


def _column(table: str, name: str) -> sa.Column[object]:
    return Base.metadata.tables[table].c[name]


# --------------------------------------------------------------------------------------
# RF-30 — a schedule may be owned by one host, or shared by the business (NULL).
# --------------------------------------------------------------------------------------


def test_schedules_carry_an_optional_owning_host() -> None:
    column = _column("schedules", "user_id")
    assert column.nullable, "NULL must stay legal: it means a shared, business-wide schedule"
    fks = list(column.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"
    assert fks[0].ondelete == "CASCADE"


# --------------------------------------------------------------------------------------
# RF-11 — external_calendar_links is alive: which calendar is read, which one is written.
# --------------------------------------------------------------------------------------


def test_calendar_links_declare_busy_and_booking_target_flags() -> None:
    busy = _column("external_calendar_links", "busy")
    target = _column("external_calendar_links", "is_booking_target")
    assert not busy.nullable and not target.nullable
    # A newly linked calendar contributes busy (safe) but is not silently made the write target.
    assert busy.server_default is not None
    assert target.server_default is not None


def test_at_most_one_booking_target_per_connection_is_a_database_constraint() -> None:
    table = Base.metadata.tables["external_calendar_links"]
    index = next(
        (idx for idx in table.indexes if idx.name == "uq_external_calendar_links_target"), None
    )
    assert index is not None, "missing the one-booking-target-per-connection unique index"
    assert index.unique
    assert tuple(col.name for col in index.columns) == ("tenant_id", "connection_id")
    # Partial on BOTH dialects: only the rows flagged as the booking target contend, so a connection
    # may still link many calendars for busy. Declared for SQLite too, or the offline suite would
    # see a FULL unique index and could not prove the semantics.
    assert index.dialect_kwargs.get("postgresql_where") is not None
    assert index.dialect_kwargs.get("sqlite_where") is not None


# --------------------------------------------------------------------------------------
# RF-11 — a booking remembers WHERE its calendar event lives, not just its id.
# --------------------------------------------------------------------------------------


def test_bookings_record_the_calendar_their_event_lives_in() -> None:
    connection = _column("bookings", "external_connection_id")
    calendar = _column("bookings", "external_calendar_id")
    assert connection.nullable and calendar.nullable  # no external calendar = both NULL
    fks = list(connection.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "external_connections"
    # SET NULL, never CASCADE: disconnecting a calendar must not delete the tenant's bookings.
    assert fks[0].ondelete == "SET NULL"
