"""transactional outbox + persisted booking iCal sequence (F1-05 / F1-08 residuals).

(A) A durable ``outbox`` queue table so a booking's post-commit effects (email / Google sync) are
persisted as *intent* rows INSIDE the booking transaction and drained by a poller — modeled on the
``webhook_deliveries`` queue (status / attempts / next_retry_at + a due index, plus a ``dedupe_key``
unique per booking for idempotent enqueue). This closes the ordering bug where an effect could fire
for a booking whose transaction later rolled back.

(B) A persisted ``sequence`` counter on ``bookings`` so each updated ``.ics`` for a UID strictly
increments SEQUENCE (RFC 5545), replacing the by-kind constant that made every reschedule emit
SEQUENCE:1.

Forward-only expand migration (RF-19): ``bookings.sequence`` is NOT NULL with a server default of 0
(every existing row reads as the confirmation sequence), and ``outbox`` is additive — both safe to
apply online. The ``downgrade`` symmetrically drops them (``sequence`` via batch mode so it works on
SQLite too), though production only ever migrates forward.

Revision ID: 0003_outbox_and_sequence
Revises: 0002_busy_cache_coverage
Create Date: 2026-07-10 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0003_outbox_and_sequence'
down_revision: str | None = '0002_busy_cache_coverage'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # (B) Persisted iCal SEQUENCE. NOT NULL + server default 0: existing bookings read as sequence 0.
    op.add_column(
        'bookings',
        sa.Column('sequence', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    # (B) Persisted, chain-stable iCal UID. NOT NULL with an empty-string server default only so the
    # ADD COLUMN is valid; every existing row is then backfilled with a UNIQUE, stable UID derived
    # from its id (so no two pre-existing bookings ever share the empty default), and the app fills a
    # real value for new rows (``default=_new_ical_uid``). A reschedule successor inherits its
    # predecessor's UID so a client honors each update to the same event.
    op.add_column(
        'bookings',
        sa.Column('ical_uid', sa.String(length=255), server_default=sa.text("''"), nullable=False),
    )
    # Backfill any pre-existing bookings with a per-row unique UID (id-derived). No-op on the
    # greenfield/empty table the tests and a fresh deploy run against, but it keeps the migration
    # correct when applied to a populated table (the concat operator differs by dialect).
    bind = op.get_bind()
    id_as_uid = (
        "id::text || '@aethercal'"
        if bind.dialect.name == 'postgresql'
        else "id || '@aethercal'"
    )
    op.execute(sa.text(f"UPDATE bookings SET ical_uid = {id_as_uid} WHERE ical_uid = ''"))

    # (A) The transactional-outbox queue, mirroring webhook_deliveries.
    op.create_table(
        'outbox',
        sa.Column('booking_id', sa.Uuid(), nullable=False),
        sa.Column('effect', sa.String(length=32), nullable=False),
        sa.Column('dedupe_key', sa.String(length=128), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['booking_id'], ['bookings.id'], name=op.f('fk_outbox_booking_id_bookings'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'], name=op.f('fk_outbox_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox')),
        sa.UniqueConstraint(
            'tenant_id', 'booking_id', 'dedupe_key',
            name=op.f('uq_outbox_tenant_id_booking_id_dedupe_key'),
        ),
    )
    op.create_index('ix_outbox_due', 'outbox', ['status', 'next_retry_at'], unique=False)
    op.create_index(op.f('ix_outbox_booking_id'), 'outbox', ['booking_id'], unique=False)
    op.create_index(op.f('ix_outbox_tenant_id'), 'outbox', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_outbox_tenant_id'), table_name='outbox')
    op.drop_index(op.f('ix_outbox_booking_id'), table_name='outbox')
    op.drop_index('ix_outbox_due', table_name='outbox')
    op.drop_table('outbox')
    # Batch mode makes DROP COLUMN work on SQLite too (it has no bare DROP COLUMN); production
    # migrates forward-only at boot, but a real downgrade keeps this revision honest.
    with op.batch_alter_table('bookings') as batch_op:
        batch_op.drop_column('ical_uid')
        batch_op.drop_column('sequence')
