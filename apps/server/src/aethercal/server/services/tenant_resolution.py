"""Resolving WHICH business a request belongs to — the one thing that must happen before the GUC.

.. rubric:: The bootstrap paradox

``api_keys`` and ``guest_tokens`` both carry a ``tenant_id``, and both are queried **without one** —
because they are the tables that *produce* it. The API key's prefix is the only thing a request
arrives with; the guest's cancel link carries a token and nothing else.

So, under a naive policy:

* ``SELECT ... FROM api_keys WHERE prefix = :p`` with no GUC → **zero rows** → ==every authenticated
  request 401s==;
* ``SELECT ... FROM guest_tokens WHERE token_hash = :h`` with no GUC → **zero rows** → ==every
  cancel/reschedule link already sitting in a guest's inbox stops working==, with no way to rebuild
  the business from the token, because only its ``sha256`` was ever stored.

You cannot stamp the GUC before reading the key, and you cannot read the key under RLS without the
GUC. Every "one GUC per request" design hangs itself right here. The ``SECURITY DEFINER`` resolvers
are not an elegance — they are the only way out.

.. rubric:: What these functions do, and what they refuse to do

Each returns a bare ``uuid`` and nothing else. The caller then binds the GUC and **re-reads the row
under RLS** to check the hash, the revocation, the expiry. Two queries, zero leaks — and, crucially,
==no policy is opened on ``api_keys``==, which would have handed key hashes, and the ability to
enumerate every business on the instance, to anybody holding one valid key.

The tokens already in guests' inboxes keep working, because the resolver translates them by hash.
==``tenant_id`` is NOT added to the token payload==: two sources of truth for one fact is exactly
how
drift is born, and the tokens already issued could not be re-signed anyway.

.. rubric:: The SQLite branch is not a shortcut

The offline suite has no roles, no RLS and no ``SECURITY DEFINER`` functions — it builds its schema
straight from ``Base.metadata``, and there is nothing there to bypass. Taking the plain read on that
dialect keeps several hundred offline service tests running exactly as they always have. On
PostgreSQL — the real path, and the only one that ships — the resolver is always the function.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import ApiKey, GuestToken, Tenant

_BY_API_KEY_PREFIX = text("SELECT aethercal_tenant_by_api_key_prefix(:value)")
_BY_GUEST_TOKEN_HASH = text("SELECT aethercal_tenant_by_guest_token_hash(:value)")
_BY_SLUG = text("SELECT aethercal_tenant_by_slug(:value)")


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


async def tenant_by_api_key_prefix(session: AsyncSession, prefix: str) -> uuid.UUID | None:
    """The business owning the key with this prefix, or ``None``. It never says why (RF-16)."""
    if not _is_postgres(session):
        return await session.scalar(select(ApiKey.tenant_id).where(ApiKey.prefix == prefix))
    return await session.scalar(_BY_API_KEY_PREFIX, {"value": prefix})


async def tenant_by_guest_token_hash(session: AsyncSession, token_hash: str) -> uuid.UUID | None:
    """The business owning the guest token with this hash, or ``None``.

    This is what keeps every cancel/reschedule link ALREADY IN A GUEST'S INBOX working on the day
    RLS
    lands. Without it those links resolve to zero rows and simply stop working — the most expensive
    compatibility break available in this project, and one with no way back: only the hash was ever
    stored, so the business cannot be reconstructed from the token by any other means.
    """
    if not _is_postgres(session):
        return await session.scalar(
            select(GuestToken.tenant_id).where(GuestToken.token_hash == token_hash)
        )
    return await session.scalar(_BY_GUEST_TOKEN_HASH, {"value": token_hash})


async def tenant_by_slug(session: AsyncSession, slug: str) -> uuid.UUID | None:
    """The business with this slug, or ``None``.

    ``tenants`` carries no policy — the admin's boot reads it before any GUC can exist, and the
    public router makes slugs semi-public by design — so on the face of it this resolver is
    unnecessary today. It exists because the callers that will need it (the public booking router,
    the inbound payment webhook, both of which carry the slug in the ROUTE) must not depend on that
    decision holding for ever. The slug is not a secret and confers no authority: it only SELECTS
    which key is going to be checked.
    """
    if not _is_postgres(session):
        return await session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    return await session.scalar(_BY_SLUG, {"value": slug})


__all__ = [
    "tenant_by_api_key_prefix",
    "tenant_by_guest_token_hash",
    "tenant_by_slug",
]
