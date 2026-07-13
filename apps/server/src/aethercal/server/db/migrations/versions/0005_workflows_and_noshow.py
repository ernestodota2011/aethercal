"""multichannel workflows, the no-show status, the generalised notification ledger, outbox leases.

Forward-only expand migration (RF-19). Everything is additive; every NOT NULL column arrives with a
``server_default`` so the ``ADD COLUMN`` needs no backfill, and the default is then dropped so the
Python-side ``default=`` stays the single source of truth (the 0003/0004 discipline).

What lands here, and why:

(A) **Workflow tables** (RF-24): ``workflows`` (the rule), ``workflow_steps`` (one message on one
    channel), ``workflow_templates`` (the body per channel/kind/locale).

(B) **``bookings.no_show_at`` + ``bookings.guest_phone``** (RF-25 / RF-24). The phone is validated as
    E.164 at the schema layer, not by the column.

(C) **The ``bookings.status`` CHECK constraint — CREATED, not widened.** The plan assumed a CHECK
    existed and needed ``no_show`` added to it. It did not exist: ``sa.Enum(..., native_enum=False)``
    defaults to ``create_constraint=False`` in SQLAlchemy 1.4+, so since 0001 the column has been a
    bare ``VARCHAR(16)`` that would accept ANY string (verified against both the live PostgreSQL 16
    schema and the SQLite one — zero CHECK constraints on the table). This creates the constraint
    that should always have been there, over all four statuses.

(D) **The generalised notification ledger.** ``sent_notifications`` gains ``channel`` and ``step_id``
    and swaps its flat ``UNIQUE (tenant_id, booking_id, kind)`` for the partial-index PAIR that can
    actually express "exactly once per booking, per kind, per channel, per step" — see the model
    docstring for why a flat five-column UNIQUE would silently do nothing whenever ``step_id`` is
    NULL, which is every message that exists today.

(E) **Outbox lease columns** (R8): ``claimed_by`` / ``lease_expires_at``, so a worker can claim a row
    and then run its network I/O with no transaction (and no row lock) held.

(F) **RF-10 moves off APScheduler and onto the outbox.** Until now the 24 h reminder ran on a SECOND
    scheduler (an APScheduler ``SQLAlchemyJobStore``) with its own idempotency barrier. Leave that in
    place alongside RF-24 and a tenant whose workflow says "email 24 h before" mails the guest TWICE
    — the two barriers (the ``SentNotification`` ledger and the outbox ``dedupe_key``) know nothing
    about each other. So:
      * the reminder becomes a SEEDED WORKFLOW RULE, one per existing tenant (``before_start``,
        ``offset_minutes = -1440``, a single ``email`` step of kind ``reminder``). The step's kind is
        deliberately ``reminder`` — the SAME value the ledger already stores — so a reminder that
        already went out stays recognised as sent and is never sent again;
      * every reminder still IN FLIGHT in the APScheduler jobstore is materialised here as an outbox
        row due at ``start_at - 24h``, so no live booking loses its reminder in the cutover. A
        booking whose reminder ALREADY went out is skipped (the ``NOT EXISTS`` on the ledger below).
    After this, the idempotency of a reminder lives in exactly one place.

Revision ID: 0005_workflows_and_noshow
Revises: 0004_event_type_translations
Create Date: 2026-07-12 10:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from alembic import op
import sqlalchemy as sa


revision: str = '0005_workflows_and_noshow'
down_revision: str | None = '0004_event_type_translations'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The full booking vocabulary (aethercal.core.model.BookingStatus). Kept LITERAL here on purpose: a
# migration must describe the schema at ITS point in history, never import a model that keeps moving.
_STATUS_CHECK = "status IN ('pending', 'confirmed', 'cancelled', 'no_show')"

# The seeded RF-10 rule. `_REMINDER_KIND` MUST stay 'reminder': it is the ledger key already written
# for every reminder ever sent, and matching it is what stops the cutover re-sending them.
_REMINDER_KIND = 'reminder'
_REMINDER_CHANNEL = 'email'
_REMINDER_WORKFLOW_NAME = '24h reminder'
_REMINDER_TRIGGER = 'before_start'
_REMINDER_OFFSET_MINUTES = -1440
_REMINDER_LEAD = timedelta(minutes=-_REMINDER_OFFSET_MINUTES)

# The in-flight reminders are materialised as plain `email` effects carrying the LEGACY dedupe key,
# not as `notify` workflow steps. Two load-bearing reasons: the email handler already exists and is
# tested (a `notify` row would dead-letter until Wave 1 lands its handler), and the legacy key keeps
# the ledger identity of these messages byte-identical to the rows already in the table.
_REMINDER_EFFECT = 'email'
_REMINDER_DEDUPE_KEY = 'email:reminder'


def _ad_hoc_tables() -> tuple[sa.Table, sa.Table, sa.Table, sa.Table]:
    """Minimal Core tables for the data migration (never the ORM — a migration is frozen in time)."""
    meta = sa.MetaData()
    workflows = sa.Table(
        'workflows', meta,
        sa.Column('id', sa.Uuid()),
        sa.Column('tenant_id', sa.Uuid()),
        sa.Column('event_type_id', sa.Uuid()),
        sa.Column('name', sa.String(255)),
        sa.Column('trigger', sa.String(32)),
        sa.Column('offset_minutes', sa.Integer()),
        sa.Column('active', sa.Boolean()),
    )
    workflow_steps = sa.Table(
        'workflow_steps', meta,
        sa.Column('id', sa.Uuid()),
        sa.Column('tenant_id', sa.Uuid()),
        sa.Column('workflow_id', sa.Uuid()),
        sa.Column('channel', sa.String(16)),
        sa.Column('kind', sa.String(32)),
        sa.Column('position', sa.Integer()),
    )
    outbox = sa.Table(
        'outbox', meta,
        sa.Column('id', sa.Uuid()),
        sa.Column('tenant_id', sa.Uuid()),
        sa.Column('booking_id', sa.Uuid()),
        sa.Column('effect', sa.String(32)),
        sa.Column('dedupe_key', sa.String(128)),
        sa.Column('payload', sa.JSON()),
        sa.Column('status', sa.String(16)),
        sa.Column('attempts', sa.Integer()),
        sa.Column('next_retry_at', sa.DateTime(timezone=True)),
    )
    sent_notifications = sa.Table(
        'sent_notifications', meta,
        sa.Column('id', sa.Uuid()),
        sa.Column('tenant_id', sa.Uuid()),
        sa.Column('booking_id', sa.Uuid()),
        sa.Column('kind', sa.String(32)),
        sa.Column('channel', sa.String(16)),
        sa.Column('step_id', sa.Uuid()),
    )
    return workflows, workflow_steps, outbox, sent_notifications


def _seed_reminder_workflows(bind: sa.Connection) -> None:
    """One ``before_start -1440`` workflow (with its single email step) per existing tenant.

    It ALSO backfills the ledger, and that is not housekeeping — it closes a duplicate-send hole.

    The generalised ledger identity is ``(tenant, booking, kind, channel, step_id)``. Every reminder
    the retired APScheduler already sent is on record with ``step_id = NULL`` (there were no steps).
    The workflow engine will write ITS reminders with ``step_id = <the seeded step>``. Those two keys
    do **not** collide — so a live booking whose reminder already went out would sail straight past
    the unique index and be reminded a SECOND time, which is precisely the bug this rework exists to
    kill.

    Of the two ways to close it (keep an extra barrier on ``(tenant, booking, kind)``, or make the
    old rows carry the new identity) this takes the second: it re-keys the legacy reminder rows onto
    the seeded step. The ledger then has ONE identity scheme, and the unique index — not a second,
    parallel rule bolted alongside it — is what stops the second send.
    """
    workflows, workflow_steps, _outbox, sent_notifications = _ad_hoc_tables()
    tenant_ids = [_as_uuid(raw) for raw in bind.execute(sa.text('SELECT id FROM tenants')).scalars()]
    for tenant_id in tenant_ids:
        workflow_id = uuid.uuid4()
        step_id = uuid.uuid4()
        bind.execute(
            sa.insert(workflows).values(
                id=workflow_id,
                tenant_id=tenant_id,
                event_type_id=None,  # applies to every event type of the tenant
                name=_REMINDER_WORKFLOW_NAME,
                trigger=_REMINDER_TRIGGER,
                offset_minutes=_REMINDER_OFFSET_MINUTES,
                active=True,
            )
        )
        bind.execute(
            sa.insert(workflow_steps).values(
                id=step_id,
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                channel=_REMINDER_CHANNEL,
                kind=_REMINDER_KIND,
                position=0,
            )
        )
        # Re-key this tenant's ALREADY-SENT reminders onto the seeded step, so the engine's future
        # insert for the same booking COLLIDES with them instead of sending a second reminder.
        # ``channel`` is already 'email' (the ADD COLUMN default); only ``step_id`` was missing.
        # A TYPED Core statement, not sa.text(): SQLite's driver cannot bind a raw ``uuid.UUID``, so
        # the value has to go through the ``sa.Uuid()`` column's bind processor.
        bind.execute(
            sa.update(sent_notifications)
            .where(
                sent_notifications.c.tenant_id == tenant_id,
                sent_notifications.c.kind == _REMINDER_KIND,
                sent_notifications.c.step_id.is_(None),
            )
            .values(step_id=step_id)
        )


def _as_utc(value: object) -> datetime:
    """Coerce a timestamp read back from raw SQL into an aware UTC ``datetime``.

    PostgreSQL returns an aware ``datetime``; SQLite returns a naive one — or, straight off a raw
    ``exec_driver_sql``, a plain string. Normalise all three before doing arithmetic on it.
    """
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _as_uuid(value: object) -> uuid.UUID:
    """Coerce an id read back from raw SQL into a real ``UUID``.

    PostgreSQL returns a ``uuid.UUID``; SQLite stores the column as ``CHAR(32)`` and hands back a
    plain string. The typed ``sa.Uuid()`` columns used for the INSERTs below bind only a real UUID,
    so a raw-SQL read has to be normalised before it can be written back.
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _migrate_in_flight_reminders(bind: sa.Connection) -> None:
    """Materialise the reminders still pending in the APScheduler jobstore as outbox rows.

    A booking qualifies when it is still ``confirmed``, still in the future, has NOT already been
    reminded (its ledger row is the proof — this is precisely what makes the cutover
    non-duplicating), and has no reminder intent queued already. The intent is due at
    ``start_at - 24h``; for a booking starting in under 24 h that lands in the past, so the very next
    drain sends it — which is exactly what the APScheduler job would have done, its fire time having
    already passed.
    """
    _workflows, _steps, outbox, _ledger = _ad_hoc_tables()
    candidates = bind.execute(
        sa.text(
            """
            SELECT b.id, b.tenant_id, b.start_at
            FROM bookings b
            WHERE b.status = 'confirmed'
              AND NOT EXISTS (
                  SELECT 1 FROM sent_notifications sn
                  WHERE sn.booking_id = b.id AND sn.kind = :kind
              )
              AND NOT EXISTS (
                  SELECT 1 FROM outbox o
                  WHERE o.booking_id = b.id AND o.dedupe_key = :dedupe_key
              )
            """
        ),
        {'kind': _REMINDER_KIND, 'dedupe_key': _REMINDER_DEDUPE_KEY},
    ).fetchall()

    now = datetime.now(UTC)
    for raw_booking_id, raw_tenant_id, start_at in candidates:
        if _as_utc(start_at) <= now:  # already happened; a reminder for it would be nonsense
            continue
        bind.execute(
            sa.insert(outbox).values(
                id=uuid.uuid4(),
                tenant_id=_as_uuid(raw_tenant_id),
                booking_id=_as_uuid(raw_booking_id),
                effect=_REMINDER_EFFECT,
                dedupe_key=_REMINDER_DEDUPE_KEY,
                payload={'kind': _REMINDER_KIND, 'locale': 'es'},
                status='pending',
                attempts=0,
                next_retry_at=_as_utc(start_at) - _REMINDER_LEAD,
            )
        )


def upgrade() -> None:
    # (A) Workflow tables. Created FIRST: sent_notifications.step_id points at workflow_steps.
    op.create_table(
        'workflows',
        sa.Column('event_type_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('trigger', sa.String(length=32), nullable=False),
        sa.Column('offset_minutes', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], name=op.f('fk_workflows_event_type_id_event_types'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_workflows_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_workflows')),
        sa.UniqueConstraint('tenant_id', 'name', name=op.f('uq_workflows_tenant_id_name')),
    )
    op.create_index(op.f('ix_workflows_event_type_id'), 'workflows', ['event_type_id'], unique=False)
    op.create_index(op.f('ix_workflows_tenant_id'), 'workflows', ['tenant_id'], unique=False)

    op.create_table(
        'workflow_steps',
        sa.Column('workflow_id', sa.Uuid(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], name=op.f('fk_workflow_steps_workflow_id_workflows'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_workflow_steps_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_steps')),
        sa.UniqueConstraint('tenant_id', 'workflow_id', 'position', name=op.f('uq_workflow_steps_tenant_id_workflow_id_position')),
    )
    op.create_index(op.f('ix_workflow_steps_workflow_id'), 'workflow_steps', ['workflow_id'], unique=False)
    op.create_index(op.f('ix_workflow_steps_tenant_id'), 'workflow_steps', ['tenant_id'], unique=False)

    op.create_table(
        'workflow_templates',
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('locale', sa.String(length=16), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_workflow_templates_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_templates')),
        sa.UniqueConstraint('tenant_id', 'channel', 'kind', 'locale', name=op.f('uq_workflow_templates_tenant_id_channel_kind_locale')),
    )
    op.create_index(op.f('ix_workflow_templates_tenant_id'), 'workflow_templates', ['tenant_id'], unique=False)

    # (B) + (C) Booking columns, and the status CHECK that never existed. Batch mode so the CHECK is
    # created on SQLite too (it has no bare ADD CONSTRAINT).
    with op.batch_alter_table('bookings') as batch_op:
        batch_op.add_column(sa.Column('guest_phone', sa.String(length=20), nullable=True))
        # Persist the consent, or the consent did not happen: a checkbox whose answer is discarded is
        # not consent, and cannot be evidenced.
        batch_op.add_column(
            sa.Column('guest_phone_consent_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column('no_show_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint('booking_status', _STATUS_CHECK)

    # (G) schedules.user_id (RF-30). NULL = the business's shared opening hours. Without this column
    # `schedules` is only tenant-scoped, so two hosts silently share one schedule — RF-30 cannot be
    # built on top of that. It rides in THIS migration because this batch owns the only one: adding it
    # elsewhere would risk a second Alembic head.
    with op.batch_alter_table('schedules') as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f('fk_schedules_user_id_users'), 'users', ['user_id'], ['id'], ondelete='CASCADE'
        )
    op.create_index(op.f('ix_schedules_user_id'), 'schedules', ['user_id'], unique=False)

    # (E) Outbox lease columns (R8).
    op.add_column('outbox', sa.Column('claimed_by', sa.String(length=64), nullable=True))
    op.add_column('outbox', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_outbox_lease', 'outbox', ['status', 'lease_expires_at'], unique=False)

    # (D) The generalised ledger. `channel` is NOT NULL with an 'email' server default so every
    # existing row reads as exactly what it was, with zero backfill; the default is then dropped so
    # the app's `default="email"` is the only source of truth. The flat legacy UNIQUE is replaced by
    # the partial pair (a flat 5-column UNIQUE would be a NO-OP wherever `step_id IS NULL`, on BOTH
    # backends — NULLs compare as distinct — silently unprotecting every message that exists today).
    # The ADD COLUMNs run OUTSIDE the batch, and that is load-bearing on SQLite: a batch block
    # collapses every operation into ONE table rebuild, so adding `channel` (NOT NULL, default
    # 'email') and dropping its default in the same block would rebuild the table with no default at
    # all — and every existing row would be copied with a NULL `channel`, violating NOT NULL. Adding
    # it first populates the existing rows with 'email'; the batch below then only has to rebuild for
    # the FK + the constraint swap, and the data comes across already filled in.
    op.add_column(
        'sent_notifications',
        sa.Column('channel', sa.String(length=16), server_default=sa.text("'email'"), nullable=False),
    )
    op.add_column('sent_notifications', sa.Column('step_id', sa.Uuid(), nullable=True))
    with op.batch_alter_table('sent_notifications') as batch_op:
        batch_op.create_foreign_key(
            op.f('fk_sent_notifications_step_id_workflow_steps'),
            'workflow_steps', ['step_id'], ['id'], ondelete='CASCADE',
        )
        batch_op.drop_constraint('uq_sent_notifications_tenant_id_booking_id_kind', type_='unique')
        batch_op.alter_column('channel', server_default=None)
    op.create_index(op.f('ix_sent_notifications_step_id'), 'sent_notifications', ['step_id'], unique=False)
    op.create_index(
        'uq_sent_notifications_kind_channel',
        'sent_notifications', ['tenant_id', 'booking_id', 'kind', 'channel'],
        unique=True,
        postgresql_where=sa.text('step_id IS NULL'),
        sqlite_where=sa.text('step_id IS NULL'),
    )
    op.create_index(
        'uq_sent_notifications_kind_channel_step',
        'sent_notifications', ['tenant_id', 'booking_id', 'kind', 'channel', 'step_id'],
        unique=True,
        postgresql_where=sa.text('step_id IS NOT NULL'),
        sqlite_where=sa.text('step_id IS NOT NULL'),
    )

    # (F) RF-10 leaves APScheduler: seed the rule, then carry the in-flight reminders over.
    bind = op.get_bind()
    _seed_reminder_workflows(bind)
    _migrate_in_flight_reminders(bind)


def downgrade() -> None:
    op.drop_index('uq_sent_notifications_kind_channel_step', table_name='sent_notifications')
    op.drop_index('uq_sent_notifications_kind_channel', table_name='sent_notifications')
    op.drop_index(op.f('ix_sent_notifications_step_id'), table_name='sent_notifications')
    with op.batch_alter_table('sent_notifications') as batch_op:
        batch_op.drop_constraint(op.f('fk_sent_notifications_step_id_workflow_steps'), type_='foreignkey')
        batch_op.drop_column('step_id')
        batch_op.drop_column('channel')
        batch_op.create_unique_constraint(
            'uq_sent_notifications_tenant_id_booking_id_kind', ['tenant_id', 'booking_id', 'kind']
        )

    op.drop_index('ix_outbox_lease', table_name='outbox')
    op.drop_column('outbox', 'lease_expires_at')
    op.drop_column('outbox', 'claimed_by')

    op.drop_index(op.f('ix_schedules_user_id'), table_name='schedules')
    with op.batch_alter_table('schedules') as batch_op:
        batch_op.drop_constraint(op.f('fk_schedules_user_id_users'), type_='foreignkey')
        batch_op.drop_column('user_id')

    with op.batch_alter_table('bookings') as batch_op:
        # The BARE name: Alembic applies the metadata naming convention (`ck_%(table_name)s_%(
        # constraint_name)s`) to whatever is passed, so handing it the already-rendered
        # `ck_bookings_booking_status` would look for `ck_bookings_ck_bookings_booking_status`.
        batch_op.drop_constraint('booking_status', type_='check')
        batch_op.drop_column('no_show_at')
        batch_op.drop_column('guest_phone_consent_at')
        batch_op.drop_column('guest_phone')

    op.drop_index(op.f('ix_workflow_templates_tenant_id'), table_name='workflow_templates')
    op.drop_table('workflow_templates')
    op.drop_index(op.f('ix_workflow_steps_tenant_id'), table_name='workflow_steps')
    op.drop_index(op.f('ix_workflow_steps_workflow_id'), table_name='workflow_steps')
    op.drop_table('workflow_steps')
    op.drop_index(op.f('ix_workflows_tenant_id'), table_name='workflows')
    op.drop_index(op.f('ix_workflows_event_type_id'), table_name='workflows')
    op.drop_table('workflows')
