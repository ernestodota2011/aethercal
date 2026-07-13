"""BYOK: the credentials a business brings to the instance, encrypted at rest (RF-27).

Until this table, every provider this product talks to was configured **once, for the whole
instance, from the environment** — one SMTP relay, one WhatsApp number, one SMS account. For mail
that is a defensible default. For MONEY it is not a default at all; it is a bug with a name: a
business's guest would pay into the *instance operator's* payment account, and the business would
send its WhatsApp from the operator's number.

So a business brings its own. ``tenant_credentials`` holds one row per (business, provider), whose
``encrypted_payload`` is a Fernet token over the JSON object of that provider's secret fields.

.. rubric:: What the encryption does, and what it does NOT do — read
   :mod:`aethercal.server.services.tenant_credentials`

The Fernet key is derived from the instance's single ``AETHERCAL_APP_SECRET``, so it is ONE key for
every business on the instance. That is encryption **at rest**, and it is not cryptographic
isolation: whoever operates the instance can decrypt any business's credential. It is written down
in full — in that module's docstring and in ``docs/byok-credentials.md`` — because a product that
promises more isolation than it delivers is worse than one that is honest about what it has.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from aethercal.server.db.encrypted import FERNET_AT_REST


class TenantCredential(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """One provider credential owned by ONE business. The payload is never stored in the clear."""

    __tablename__ = "tenant_credentials"

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    """The provider this credential is for — the value of a
    :class:`~aethercal.server.services.tenant_credentials.CredentialProvider`.

    A ``String``, deliberately, and not a database ``Enum``: the set of providers grows with the
    payment adapters (a second processor is a wave of its own), and an ``ALTER TYPE`` per adapter
    buys nothing here. The vocabulary is enforced where it can be enforced *exhaustively* — in the
    service, where a new provider does not type-check until somebody has declared whether it handles
    MONEY, and which fields it requires."""

    encrypted_payload: Mapped[bytes] = mapped_column(
        sa.LargeBinary, nullable=False, info={FERNET_AT_REST: True}
    )
    """A Fernet token over ``{"field": "value", ...}`` — the provider's secret fields, as JSON.

    ==The ``info`` marker is load-bearing.== It is what puts this column into
    :func:`~aethercal.server.db.encrypted.encrypted_columns`, and therefore into the key rotation. A
    column of ciphertext the rotation cannot see is data the instance loses on the day the old
    secret is retired."""

    __table_args__ = (
        # ONE credential per provider per business. Replacing a business's payment account is an
        # UPDATE of this row, not a second row nobody can choose between — "which of these two
        # accounts do we charge into?" is not a question this system will ever have to answer.
        # Removing the row is the OFF switch, and for a money provider that means: this business
        # stops charging. It does NOT mean "fall back to the instance's account" — see the service.
        sa.UniqueConstraint("tenant_id", "provider"),
    )


__all__ = ["TenantCredential"]
