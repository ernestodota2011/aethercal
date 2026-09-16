"""Phone verification OTP and instance suppression table (C-02b).

Adds:
- ``bookings.guest_phone_verified_at``: NULL = unverified phone possession.
- ``phone_verification_challenges``: Short-lived OTP codes (TTL 10 min, max 5 attempts),
  with tombstone retention for 24-hour rate limit counting (D-7·bis).
- ``phone_suppressions``: Instance-level opt-out table with phone_hmac under
  AETHERCAL_SUPPRESSION_KEY (D-12).

Revision ID: 0018_phone_verification_otp
Revises: 0017_outbox_skip_reason
Create Date: 2026-09-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aethercal.server.db.rls import disable_rls, enable_rls, grant_table

# 27 characters — fits alembic_version.version_num VARCHAR(32)
revision: str = "0018_phone_verification_otp"
down_revision: str | None = "0017_outbox_skip_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add guest_phone_verified_at to bookings
    op.add_column(
        "bookings",
        sa.Column("guest_phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Create phone_verification_challenges
    op.create_table(
        "phone_verification_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("phone_hmac", sa.String(length=64), nullable=False),
        sa.Column("code_hmac", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_phone_verification_challenges_booking_id",
        "phone_verification_challenges",
        ["booking_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_verification_challenges_phone_hmac",
        "phone_verification_challenges",
        ["phone_hmac"],
        unique=False,
    )
    op.create_index(
        "ix_phone_verification_challenges_source_ip",
        "phone_verification_challenges",
        ["source_ip"],
        unique=False,
    )
    op.create_index(
        "ix_phone_verification_challenges_tenant_id",
        "phone_verification_challenges",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_verification_challenges_phone_window",
        "phone_verification_challenges",
        ["tenant_id", "phone_hmac", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_phone_verification_challenges_ip_window",
        "phone_verification_challenges",
        ["source_ip", "created_at"],
        unique=False,
    )

    # 3. Create phone_suppressions
    op.create_table(
        "phone_suppressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone_hmac", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_phone_suppressions_phone_hmac",
        "phone_suppressions",
        ["phone_hmac"],
        unique=True,
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no row-level security; the offline parity suite compares columns only.
        return

    # ``phone_verification_challenges`` is tenant-scoped and takes the same belt as every other
    # scoped table: ENABLE + FORCE + the isolation policy (a table whose RLS is missing reads
    # zero rows under the policy test, which is how criterion 13 catches a migration that forgot).
    for statement in (
        *enable_rls("phone_verification_challenges"),
        *grant_table("phone_verification_challenges"),
    ):
        op.execute(statement)

    # ``phone_suppressions`` has no tenant_id to scope — it is the instance-level D-12 opt-out —
    # so it takes grants only. A policy here would hide other tenants' opt-outs behind the GUC of
    # whichever business happened to be stamped, and let a suppress-requested number be messaged.
    for statement in grant_table("phone_suppressions"):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for statement in disable_rls("phone_verification_challenges"):
            op.execute(statement)

    op.drop_index("ix_phone_suppressions_phone_hmac", table_name="phone_suppressions")
    op.drop_table("phone_suppressions")

    op.drop_index(
        "ix_phone_verification_challenges_ip_window",
        table_name="phone_verification_challenges",
    )
    op.drop_index(
        "ix_phone_verification_challenges_phone_window",
        table_name="phone_verification_challenges",
    )
    op.drop_index(
        "ix_phone_verification_challenges_tenant_id",
        table_name="phone_verification_challenges",
    )
    op.drop_index(
        "ix_phone_verification_challenges_source_ip",
        table_name="phone_verification_challenges",
    )
    op.drop_index(
        "ix_phone_verification_challenges_phone_hmac",
        table_name="phone_verification_challenges",
    )
    op.drop_index(
        "ix_phone_verification_challenges_booking_id",
        table_name="phone_verification_challenges",
    )
    op.drop_table("phone_verification_challenges")

    op.drop_column("bookings", "guest_phone_verified_at")
