"""Attendance confirmation stamp for interactive WhatsApp replies (Horizon 1).

Adds ``bookings.attendance_confirmed_at``: the instant a guest answered "1" to the reminder.

Before this column the confirm path was a NO-OP WEARING A SUCCESS MESSAGE: the handler returned
``status="attendance_confirmed"``, logged a line, and wrote nothing anywhere. The reply the guest
received promised something the database never recorded, and the operator's no-show triage could
not tell a guest who confirmed from one who was never asked.

Nullability is the whole model: NULL = no confirmation on record. The value is a fact about THIS
booking (like ``confirmed_at`` and ``no_show_at``), so it is not a ``guest_*`` column and is not
redacted by ``guest purge`` — it carries no identifier, only an instant.

Revision ID: 0019_attendance_confirmed
Revises: 0018_phone_verification_otp
Create Date: 2026-09-16 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# 24 characters — fits alembic_version.version_num VARCHAR(32)
revision: str = "0019_attendance_confirmed"
down_revision: str | None = "0018_phone_verification_otp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("attendance_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bookings", "attendance_confirmed_at")
