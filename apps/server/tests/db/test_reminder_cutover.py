"""RF-10 leaves APScheduler: the cutover must not send anybody a second reminder (migration 0005).

Until now the 24 h reminder ran on a SECOND scheduler (an APScheduler ``SQLAlchemyJobStore``) with
its OWN idempotency barrier. Alongside RF-24 that is a duplicate-send waiting to happen: a tenant
defines "email 24 h before", and the guest gets two — the ``SentNotification`` ledger and the outbox
``dedupe_key`` never knew about each other.

So 0005 retires it, and the cutover has to be exactly right for the bookings that are LIVE right
now:

1. a future booking whose reminder has NOT gone out yet gets its reminder materialised as an outbox
   row due at ``start - 24h`` — nobody silently loses a reminder because the jobstore was dropped;
2. a booking whose reminder ALREADY went out gets **nothing** — and its ledger row is re-keyed onto
   the seeded workflow step, so the workflow engine's future insert COLLIDES with it instead of
   sending a second one. (The new ledger identity includes ``step_id``; the legacy rows have
   ``step_id IS NULL``, and NULLs do not collide — so without this backfill the guest would be
   reminded twice, which is the very bug this rework exists to kill.)
3. a booking already in the past gets nothing (a reminder for it would be nonsense).

These run the REAL migration against a throwaway SQLite file, so they execute on every CI cell."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from aethercal.server.db.migrate import make_alembic_config

_BEFORE_0005 = "0004_event_type_translations"
_REMINDER_DEDUPE_KEY = "email:reminder"


def _engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'cutover.sqlite'}")


def _seed_pre_0005(engine: sa.Engine) -> dict[str, uuid.UUID]:
    """Bring the schema up to just BEFORE 0005 and plant the state a live deployment would have."""
    command.upgrade(make_alembic_config(str(engine.url)), _BEFORE_0005)

    ids = {
        "tenant": uuid.uuid4(),
        "user": uuid.uuid4(),
        "schedule": uuid.uuid4(),
        "event_type": uuid.uuid4(),
        "future_unreminded": uuid.uuid4(),
        "future_reminded": uuid.uuid4(),
        "past": uuid.uuid4(),
    }
    now = datetime.now(UTC)

    def _booking_sql(booking_id: uuid.UUID, start: datetime) -> str:
        end = start + timedelta(minutes=30)
        return (
            "INSERT INTO bookings (id, tenant_id, event_type_id, start_at, end_at, status, "
            "guest_name, guest_email, guest_timezone, answers, ical_uid, sequence) VALUES "
            f"('{booking_id.hex}', '{ids['tenant'].hex}', '{ids['event_type'].hex}', "
            f"'{start.replace(tzinfo=None).isoformat(sep=' ')}', "
            f"'{end.replace(tzinfo=None).isoformat(sep=' ')}', "
            f"'confirmed', 'Ada', 'ada@example.com', 'UTC', '{{}}', "
            f"'{booking_id}@aethercal', 0)"
        )

    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"INSERT INTO tenants (id, slug, name) VALUES ('{ids['tenant'].hex}', 'acme', 'Acme')"
        )
        conn.exec_driver_sql(
            "INSERT INTO users (id, tenant_id, email, name, timezone) VALUES "
            f"('{ids['user'].hex}', '{ids['tenant'].hex}', 'h@example.com', 'Host', 'UTC')"
        )
        conn.exec_driver_sql(
            "INSERT INTO schedules (id, tenant_id, name, timezone, rules) VALUES "
            f"('{ids['schedule'].hex}', '{ids['tenant'].hex}', 'Weekly', 'UTC', '{{}}')"
        )
        # 0004 drops the server default on the *_translations columns (the app's default=dict is the
        # single source of truth), so a raw INSERT has to supply them.
        conn.exec_driver_sql(
            "INSERT INTO event_types (id, tenant_id, host_id, schedule_id, slug, title, "
            "duration_seconds, max_advance_seconds, questions, active, title_translations, "
            "description_translations) VALUES "
            f"('{ids['event_type'].hex}', '{ids['tenant'].hex}', '{ids['user'].hex}', "
            f"'{ids['schedule'].hex}', 'intro', 'Intro', 1800, 2592000, '[]', 1, '{{}}', '{{}}')"
        )
        # A live booking a week out, never reminded.
        conn.exec_driver_sql(_booking_sql(ids["future_unreminded"], now + timedelta(days=7)))
        # A live booking tomorrow whose reminder ALREADY went out (the old jobstore fired it).
        conn.exec_driver_sql(_booking_sql(ids["future_reminded"], now + timedelta(hours=20)))
        conn.exec_driver_sql(
            "INSERT INTO sent_notifications (id, tenant_id, booking_id, kind, sent_at) VALUES "
            f"('{uuid.uuid4().hex}', '{ids['tenant'].hex}', '{ids['future_reminded'].hex}', "
            f"'reminder', '{now.replace(tzinfo=None).isoformat(sep=' ')}')"
        )
        # A booking that already happened.
        conn.exec_driver_sql(_booking_sql(ids["past"], now - timedelta(days=3)))
    return ids


def _outbox_reminders(engine: sa.Engine) -> dict[str, tuple[str, str | None]]:
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT booking_id, effect, next_retry_at FROM outbox "
            f"WHERE dedupe_key = '{_REMINDER_DEDUPE_KEY}'"
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_a_live_booking_whose_reminder_already_went_out_never_gets_a_second(tmp_path: Path) -> None:
    """The bug the whole rework exists to kill. This booking is still live and starts in 20 h, and
    its reminder has already been sent by the retired scheduler. The cutover must not queue another.
    """
    engine = _engine(tmp_path)
    ids = _seed_pre_0005(engine)

    command.upgrade(make_alembic_config(str(engine.url)), "head")

    reminders = _outbox_reminders(engine)
    assert ids["future_reminded"].hex not in reminders, (
        "the cutover queued a SECOND reminder for a booking that was already reminded"
    )

    # And the ledger row is re-keyed onto the seeded step, so the workflow engine's own insert for
    # this booking collides with it. The new identity carries step_id; the legacy row had NULL, and
    # NULLs never collide — which is exactly how a second reminder would otherwise slip through.
    with engine.begin() as conn:
        step_id = conn.exec_driver_sql(
            "SELECT id FROM workflow_steps WHERE kind = 'reminder'"
        ).scalar_one()
        ledger = conn.exec_driver_sql(
            "SELECT channel, step_id FROM sent_notifications WHERE booking_id = "
            f"'{ids['future_reminded'].hex}' AND kind = 'reminder'"
        ).one()
    assert ledger[0] == "email"
    assert ledger[1] == step_id, "the legacy reminder was NOT re-keyed onto the seeded step"
    engine.dispose()


def test_a_live_unreminded_booking_keeps_its_reminder_through_the_cutover(tmp_path: Path) -> None:
    """Nobody silently loses a reminder because the jobstore was dropped: the pending job becomes an
    outbox row due at ``start - 24h`` (the outbox's ``next_retry_at`` IS the send time)."""
    engine = _engine(tmp_path)
    ids = _seed_pre_0005(engine)

    command.upgrade(make_alembic_config(str(engine.url)), "head")

    reminders = _outbox_reminders(engine)
    assert ids["future_unreminded"].hex in reminders
    effect, next_retry_at = reminders[ids["future_unreminded"].hex]
    assert effect == "email"  # the handler that already exists and is already tested
    assert next_retry_at is not None

    with engine.begin() as conn:
        start = conn.exec_driver_sql(
            f"SELECT start_at FROM bookings WHERE id = '{ids['future_unreminded'].hex}'"
        ).scalar_one()
    due = datetime.fromisoformat(str(next_retry_at))
    assert due == datetime.fromisoformat(str(start)) - timedelta(hours=24)
    engine.dispose()


def test_a_booking_in_the_past_gets_no_reminder(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ids = _seed_pre_0005(engine)

    command.upgrade(make_alembic_config(str(engine.url)), "head")

    assert ids["past"].hex not in _outbox_reminders(engine)
    engine.dispose()


def test_the_cutover_seeds_the_reminder_rule_as_a_workflow(tmp_path: Path) -> None:
    """RF-10 stops being special-cased code and becomes a tenant-editable rule — which is what makes
    it possible for there to be exactly ONE thing that can decide to remind a guest."""
    engine = _engine(tmp_path)
    _seed_pre_0005(engine)

    command.upgrade(make_alembic_config(str(engine.url)), "head")

    with engine.begin() as conn:
        workflow = conn.exec_driver_sql(
            "SELECT trigger, offset_minutes, active, event_type_id FROM workflows"
        ).one()
        step = conn.exec_driver_sql("SELECT channel, kind, position FROM workflow_steps").one()
    assert workflow[0] == "before_start"
    assert workflow[1] == -1440  # 24 h before the start, signed
    assert workflow[2] == 1
    assert workflow[3] is None  # applies to every event type of the tenant
    assert step == ("email", "reminder", 0)
    engine.dispose()


# --------------------------------------------------------------------------------------
# The downgrade must survive data the NEW schema legitimately produces.
# --------------------------------------------------------------------------------------


def _seed_two_channel_sends(engine: sa.Engine) -> tuple[str, str]:
    """One booking, one kind, TWO channels — an email AND a WhatsApp reminder.

    Perfectly valid under the new ledger (it is the whole point of RF-24), and completely
    unrepresentable under the old one. Returns (older_id, newer_id).
    """
    ids = _seed_pre_0005(engine)
    command.upgrade(make_alembic_config(str(engine.url)), "head")

    booking_id = ids["future_unreminded"].hex
    older, newer = uuid.uuid4().hex, uuid.uuid4().hex
    step_id = uuid.uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO workflow_steps (id, tenant_id, workflow_id, channel, kind, position) "
            "SELECT "
            f"'{step_id}', tenant_id, id, 'whatsapp', 'reminder', 1 FROM workflows LIMIT 1"
        )
        conn.exec_driver_sql(
            "INSERT INTO sent_notifications (id, tenant_id, booking_id, kind, channel, step_id, "
            f"sent_at) VALUES ('{older}', '{ids['tenant'].hex}', '{booking_id}', 'reminder', "
            f"'email', NULL, '{(now - timedelta(hours=2)).isoformat(sep=' ')}')"
        )
        conn.exec_driver_sql(
            "INSERT INTO sent_notifications (id, tenant_id, booking_id, kind, channel, step_id, "
            f"sent_at) VALUES ('{newer}', '{ids['tenant'].hex}', '{booking_id}', 'reminder', "
            f"'whatsapp', '{step_id}', '{now.isoformat(sep=' ')}')"
        )
    return older, newer


def test_the_downgrade_runs_on_data_the_new_schema_legitimately_produces(tmp_path: Path) -> None:
    """==The rollback you only discover is broken mid-incident.==

    The downgrade restores the flat UNIQUE (tenant, booking, kind). But the new schema deliberately
    allows the SAME kind on TWO channels — email + WhatsApp for one reminder. Hand those valid rows
    to the old constraint and the CREATE UNIQUE fails, so the downgrade does not run at all.

    It must reduce the data first: one row per key, the OLDEST kept."""
    engine = _engine(tmp_path)
    older, newer = _seed_two_channel_sends(engine)

    # The pre-condition the OLD schema cannot represent: two rows, same kind, different channels.
    with engine.begin() as conn:
        clashing = conn.exec_driver_sql(
            "SELECT id FROM sent_notifications WHERE kind = 'reminder' "
            f"AND id IN ('{older}', '{newer}')"
        ).fetchall()
    assert len(clashing) == 2

    command.downgrade(make_alembic_config(str(engine.url)), _BEFORE_0005)

    with engine.begin() as conn:
        survivors = {
            row[0]
            for row in conn.exec_driver_sql(
                f"SELECT id FROM sent_notifications WHERE id IN ('{older}', '{newer}')"
            ).fetchall()
        }
        columns = {
            col[1]
            for col in conn.exec_driver_sql("PRAGMA table_info(sent_notifications)").fetchall()
        }

    # It RAN — and it kept exactly one row for that key: the OLDEST, deliberately.
    assert survivors == {older}, "the downgrade kept the wrong row (or blew up on valid data)"
    assert newer not in survivors  # the other channel's send is gone, on purpose
    assert "channel" not in columns and "step_id" not in columns
    engine.dispose()


def test_the_downgrade_is_a_no_op_when_there_is_nothing_to_reduce(tmp_path: Path) -> None:
    """The common case: one send per kind. Nothing is deleted, and it still runs clean."""
    engine = _engine(tmp_path)
    ids = _seed_pre_0005(engine)
    command.upgrade(make_alembic_config(str(engine.url)), "head")

    command.downgrade(make_alembic_config(str(engine.url)), _BEFORE_0005)

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT booking_id FROM sent_notifications WHERE kind = 'reminder'"
        ).fetchall()
    # The single pre-existing reminder survived untouched.
    assert [row[0] for row in rows] == [ids["future_reminded"].hex]
    engine.dispose()
