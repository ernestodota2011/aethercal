"""A key rotation must not silently overwrite a credential updated concurrently (PostgreSQL only).

``db``-marked, and for the same reason as ``test_calendar_target_concurrency.py``: the guarantee
lives in server-side concurrency control (``SELECT ... FOR UPDATE``), which SQLite cannot exercise —
it serialises writers anyway and renders ``FOR UPDATE`` as nothing at all.

.. rubric:: The lost update this closes

The rotation reads every credential's ciphertext, re-encrypts it under the new key, and writes it
back by row id. Between the read and the write, another transaction can update that same credential
(a business rotating its own payment key). Without a lock taken AT READ TIME, the rotation then
writes the re-encryption of the STALE value on top of the concurrent update — the update is gone,
with a green ``rotated N row(s)`` to say otherwise. For a payment credential that is money going to
an account nobody chose.

``SELECT ... FOR UPDATE`` closes the window: the rotation locks each row as it reads it, so a
concurrent update WAITS for the rotation to commit and then applies on top — nothing is lost. The
rotation runs as the OWNER (BYPASSRLS, to see every business at once); the concurrent update runs as
the APP role with its business bound, which is the real write path — and ``FOR UPDATE`` on the
owner's read makes the app's write block until the owner commits.

.. rubric:: Every secret here is synthetic

``sk_test_NOT_A_REAL_KEY_original`` is not a redaction of anything.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aethercal.server.crypto import decrypt_secret, derive_fernet_key
from aethercal.server.db.engine import build_sessionmaker
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import Tenant, TenantCredential
from aethercal.server.services.key_rotation import rotate_fernet_key
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential

pytestmark = pytest.mark.db

TenantFactory = Callable[..., Awaitable[Tenant]]

OLD = derive_fernet_key("the-old-app-secret")
NEW = derive_fernet_key("the-new-app-secret")

STRIPE_ORIGINAL = {
    "secret_key": "sk_test_NOT_A_REAL_KEY_original",
    "webhook_secret": "whsec_FAKE_original",
}
STRIPE_UPDATED = {
    "secret_key": "sk_test_NOT_A_REAL_KEY_updated",
    "webhook_secret": "whsec_FAKE_updated",
}

# How long the concurrent writer holds its row lock. It only has to comfortably exceed the time the
# rotation needs to reach (and block on) that row — two localhost round trips — so the contention
# is real rather than a scheduling accident. Generous on purpose; the rotation blocks the instant it
# reaches the row and is released the moment this elapses.
_HOLD_SECONDS = 0.5


async def test_a_concurrent_credential_update_is_not_lost_to_a_rotation(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
    tenant_factory: TenantFactory,
) -> None:
    """==The lost update, reproduced and closed.==

    A business updates its own Stripe key at the same moment the operator rotates the instance key.
    With the rotation's read taking ``FOR UPDATE``, the update WAITS for the rotation and lands on
    top, so the final credential is the business's new one. Without it, the rotation overwrites the
    update with the re-encryption of the old value and this assertion fails.
    """
    async with owner_maker() as session, session.begin():
        tenant = await tenant_factory(session)
        tenant_id = tenant.id
        await store_credential(
            session,
            tenant_id=tenant_id,
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_ORIGINAL,
            fernet_key=OLD,
        )

    row_is_locked = asyncio.Event()

    async def update_the_credential_concurrently() -> None:
        """The APP-role write path: bind the business, replace its credential, hold the lock.

        The hold is what makes the race deterministic — the rotation is guaranteed to reach this row
        while it is still locked, rather than only sometimes.
        """
        async with app_maker() as session:
            await bind_tenant(session, tenant_id)
            await store_credential(
                session,
                tenant_id=tenant_id,
                provider=CredentialProvider.STRIPE,
                secrets=STRIPE_UPDATED,
                fernet_key=NEW,
            )
            row_is_locked.set()
            await asyncio.sleep(_HOLD_SECONDS)
            await session.commit()

    async def rotate_after_the_row_is_locked() -> None:
        """The OWNER-role rotation. It starts once the writer holds the lock, so its read has to
        contend for the row rather than racing ahead of the writer."""
        await row_is_locked.wait()
        async with owner_maker() as session, session.begin():
            await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

    await asyncio.gather(update_the_credential_concurrently(), rotate_after_the_row_is_locked())

    async with owner_maker() as session:
        row = (await session.scalars(sa.select(TenantCredential))).one()
    surviving = json.loads(decrypt_secret(row.encrypted_payload, NEW).decode())
    assert surviving == STRIPE_UPDATED, (
        "the concurrent update was overwritten by the rotation — a lost update on a payment "
        "credential"
    )


async def test_the_rotation_reads_each_credential_row_for_update(
    owner_engine: AsyncEngine,
) -> None:
    """==The lock, proved by the SQL the rotation actually emits — deterministic, no timing.==

    Companion to the race above: the lost update is only closed if the SELECT that reads each
    credential row locks it AT READ TIME. This captures every statement the rotation sends (over an
    empty table, so nothing is seeded and nothing can flake) and asserts the credential read carries
    ``FOR UPDATE``.
    """
    statements: list[str] = []

    @sa.event.listens_for(owner_engine.sync_engine, "before_cursor_execute")
    def _capture(*args: object) -> None:
        # before_cursor_execute(conn, cursor, statement, parameters, context, executemany): the SQL
        # text is the third positional. Only the text is kept — never the parameters (a secret could
        # ride in a bound value, and none of it is needed here).
        statements.append(str(args[2]))

    maker = build_sessionmaker(owner_engine)
    async with maker() as session, session.begin():
        await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

    credential_reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "tenant_credentials" in statement
    ]
    assert credential_reads, (
        "the rotation must SELECT the credential rows in order to re-encrypt them"
    )
    assert all("FOR UPDATE" in statement.upper() for statement in credential_reads), (
        "the rotation's credential read must take FOR UPDATE: without the row lock, a credential "
        "updated between the read and the re-encrypting write is silently overwritten"
    )
