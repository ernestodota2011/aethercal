"""bookings.source_ip — the column a REQUIRED cap has been counting on, and never had.

``DailyCaps.per_ip`` is required configuration: a phone channel refuses to boot without it. And
until this migration it enforced **nothing at all**, because no client address ever reached the send
path — ``bookings`` had nowhere to record one. The module said so in its own docstring and logged a
warning at every boot, which is the most an honest no-op can do about itself.

This is the FIRST of the three pieces that close it. The other two are ``guard.enforce_ip_cap`` and
its call inside the NOTIFY handler's read phase: ==a column on its own would leave the cap *looking*
applied while still denying nothing==, which is strictly worse than the gap it replaces.

.. rubric:: Nullable, and it stays nullable

NULL means "this booking did not come through the public form". The host booking a guest by hand
from the admin, and the tenant's own API key, have no client address — and a booking with no address
is NOT capped. Backfilling the existing rows with anything at all would be inventing evidence about
where a real person was, so they stay NULL.

.. rubric:: 45 characters

The longest textual IPv6 form is the IPv4-mapped ``::ffff:255.255.255.255``, at 45. The value has
been through ``ipaddress.ip_address`` before it is written, so nothing that is not an address can
occupy this column.

.. rubric:: The revision id is 22 characters

``alembic_version.version_num`` is ``VARCHAR(32)``. A longer id passes the whole offline suite
(SQLite does not enforce the length) and dies in production, at boot, on PostgreSQL — which is how
``0006`` was found. ``tests/test_alembic_config.py`` guards every id offline; this is why the number
was RESERVED in the batch spec instead of being invented here, with four cuts landing in parallel.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_booking_source_ip"
down_revision: str | None = "0008_rls_roles_and_policies"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("source_ip", sa.String(length=45), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "source_ip")
