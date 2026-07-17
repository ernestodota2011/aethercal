"""The rotation READ WINDOW (B-03): every consumer decrypts under BOTH keys, writes under the new.

The Fernet key is derived from ``AETHERCAL_APP_SECRET``. A rotation re-encrypts every stored secret
from the retiring key onto the new one — but the app's own encrypt/decrypt held ONE key. So between
"deploy the new secret" and "the rotation finishes", a process that could only decrypt with the new
key could not read a row still on the old one, and — worse — it would WRITE with the new key while
other rows sat on the old, or (before the new secret reached it) write under the OLD key onto rows
the rotation had already moved. Either way a credential could end up unreadable once the old secret
is retired: a payment key, gone.

The fix makes decrypt try the CURRENT key and, during the window, the PREVIOUS one too, while every
write stays on the current key. So a process restarted with ``AETHERCAL_APP_SECRET``=new +
``AETHERCAL_PREVIOUS_APP_SECRET``=old reads a row written under EITHER key, and there is no window
in which it must write under the key about to be retired.

This proves it for EVERY consumer that decrypts — the tenant BYOK credential, the Google calendar
connection, and the webhook subscriber secret — because a fix that reached only one of them would
leave the others broken in exactly the same window.

.. rubric:: ==Every secret here is synthetic.== ``sk_test_NOT_A_REAL_KEY`` is not a redaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.webhooks import WebhookCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant, User
from aethercal.server.services.calendars import (
    GoogleCredential,
    load_credentials,
    store_google_connection,
)
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    resolve_money_credential,
    store_credential,
)
from aethercal.server.services.webhooks import create_webhook, decrypt_webhook_secret

TenantFactory = Callable[..., Awaitable[Tenant]]

# The secret being retired (OLD) and the one replacing it (NEW). A row written before the rotation
# reached it is on OLD; every write from a restarted process is on NEW.
NEW = derive_fernet_key("new-app-secret")
OLD = derive_fernet_key("old-app-secret")

# What a process restarted with AETHERCAL_APP_SECRET=new + AETHERCAL_PREVIOUS_APP_SECRET=old holds:
# the current key FIRST (every write uses it), the retiring one after (rows not yet rotated).
WINDOW = [NEW, OLD]

STRIPE = {"secret_key": "sk_test_NOT_A_REAL_KEY_window", "webhook_secret": "whsec_FAKE_window"}
TOKEN_JSON = '{"token": "at-window", "refresh_token": "rt-window"}'


async def test_a_tenant_credential_written_under_the_old_key_is_read_under_both(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """A BYOK payment credential the rotation has not reached — still on OLD — resolves under the
    window, and does NOT under the new key alone."""
    tenant = await tenant_factory(sqlite_session)
    await store_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.STRIPE,
        secrets=STRIPE,
        fernet_key=OLD,
    )
    await sqlite_session.flush()

    resolved = await resolve_money_credential(
        sqlite_session,
        tenant_id=tenant.id,
        provider=CredentialProvider.STRIPE,
        fernet_key=WINDOW,
    )
    assert dict(resolved.secrets) == STRIPE

    with pytest.raises(InvalidToken):
        await resolve_money_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            fernet_key=NEW,
        )


async def test_a_webhook_secret_written_under_the_old_key_is_read_under_both(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The delivery worker decrypts the subscriber secret to sign the payload — it must read a
    subscription created before the rotation, still on OLD, throughout the window."""
    tenant = await tenant_factory(sqlite_session)
    webhook, secret = await create_webhook(
        sqlite_session,
        tenant_id=tenant.id,
        params=WebhookCreate.model_validate(
            {
                "url": "https://consumer.test/hook",
                "events": ["booking.created"],
                "secret": "plaintext-window-secret",
            }
        ),
        fernet_key=OLD,
    )

    assert decrypt_webhook_secret(webhook, WINDOW) == secret.encode("utf-8")

    with pytest.raises(InvalidToken):
        decrypt_webhook_secret(webhook, NEW)


async def test_a_calendar_token_written_under_the_old_key_is_read_under_both(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """The busy-cache refresh / booking-sync ticks decrypt the host's Google token to call Google.
    A connection stored before the rotation is on OLD, and the ticks read it under the window."""
    tenant = await tenant_factory(sqlite_session)
    user = (await sqlite_session.scalars(sa.select(User).where(User.tenant_id == tenant.id))).one()
    connection = await store_google_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=user.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json=TOKEN_JSON),
        fernet=Fernet(OLD),
    )
    await sqlite_session.flush()

    reader = MultiFernet([Fernet(NEW), Fernet(OLD)])
    assert load_credentials(connection, fernet=reader) == TOKEN_JSON

    with pytest.raises(InvalidToken):
        load_credentials(connection, fernet=Fernet(NEW))
