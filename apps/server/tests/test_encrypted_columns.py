"""==A column of ciphertext the key rotation cannot see is data the instance LOSES.==

The rotation walks :func:`~aethercal.server.db.encrypted.encrypted_columns`, which is derived from a
marker on the model. So the question that matters is not "does the rotation work" — it is "can a
column of ciphertext exist that the rotation never hears about?".

It can, and the way it happens is entirely ordinary: somebody adds an encrypted column and does not
know the marker exists. Nothing fails. The rotation runs, reports success, the operator retires the
old app secret — and that column is now encrypted under a secret that exists nowhere any more.
==The data is gone, and the message arrives weeks later, from a completely different part of the
system.==

So the marker is not trusted to be remembered. It is CHECKED, against the one thing a forgetful
author cannot avoid declaring: the column's TYPE.
"""

from __future__ import annotations

import sqlalchemy as sa

from aethercal.server.db import Base
from aethercal.server.db.encrypted import (
    FERNET_AT_REST,
    EncryptedColumn,
    binary_columns,
    encrypted_columns,
)

# Every Fernet-encrypted column in the product, as of this wave. A hand-written list — and here, in
# a test, being a photograph is exactly the job: it is the second pair of eyes on the derivation, so
# that a derivation which silently began returning nothing (a renamed marker, a refactor of
# ``info``) could not pass by agreeing with itself.
KNOWN = {
    EncryptedColumn(table="webhooks", column="secret", primary_key="id"),
    EncryptedColumn(table="external_connections", column="encrypted_credentials", primary_key="id"),
    EncryptedColumn(table="tenant_credentials", column="encrypted_payload", primary_key="id"),
}


def test_every_binary_column_declares_whether_it_is_encrypted() -> None:
    """==The belt.== A new ``LargeBinary`` column that forgets the marker FAILS RIGHT HERE.

    The two sets are compared, not each merely checked for plausibility: the marked columns and the
    binary columns must be the SAME set.

    If you are reading this because the test just went red, one of two things has happened:

    * you added an encrypted column and did not mark it → add ``info={FERNET_AT_REST: True}``, and
      the key rotation picks it up on the day it lands rather than losing it on the day the old
      secret is retired;
    * you added a binary column that genuinely holds no secret (an avatar, a PDF) → then say so
      here, out loud, as an exception. A decision somebody made, rather than a marker somebody
      forgot.
    """
    marked = set(encrypted_columns(Base.metadata))
    binary = set(binary_columns(Base.metadata))

    assert binary, "the derivation itself is empty — it would pass by measuring nothing"
    assert marked == binary, (
        "these binary columns do not declare whether they hold Fernet ciphertext: "
        f"{sorted(binary - marked)}. An unmarked encrypted column is invisible to the key "
        "rotation, and therefore unreadable for ever once the old app secret is retired."
    )


def test_the_derivation_finds_the_columns_we_know_about() -> None:
    """The photograph, checked against the derivation — so that neither can be wrong on its own."""
    assert set(encrypted_columns(Base.metadata)) == KNOWN


def test_an_unmarked_binary_column_is_detected() -> None:
    """The mechanism itself, proved on a throwaway table. ==The guard has to be able to BITE.==

    A check that only ever runs against a codebase where every column is already marked proves the
    current state is fine — it does not prove it would NOTICE if it were not. So: a table whose
    ciphertext column forgot the marker. The derivation must not return it, and the comparison the
    test above makes must therefore fail.
    """
    metadata = sa.MetaData()
    sa.Table(
        "forgotten",
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("ciphertext", sa.LargeBinary, nullable=False),  # no info={FERNET_AT_REST: True}
    )

    assert binary_columns(metadata) == (
        EncryptedColumn(table="forgotten", column="ciphertext", primary_key="id"),
    )
    assert encrypted_columns(metadata) == ()
    assert set(binary_columns(metadata)) != set(encrypted_columns(metadata))


def test_a_marked_column_is_found_by_the_derivation() -> None:
    """And the positive control: with the marker, the same column IS found."""
    metadata = sa.MetaData()
    sa.Table(
        "remembered",
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("ciphertext", sa.LargeBinary, nullable=False, info={FERNET_AT_REST: True}),
    )

    assert encrypted_columns(metadata) == (
        EncryptedColumn(table="remembered", column="ciphertext", primary_key="id"),
    )
