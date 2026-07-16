"""Per-business branding (RF-27) — read and write the four columns a guest actually sees.

.. rubric:: ==This service IS the isolation belt for ``tenants``, and it is the only one there is==

Every other table in this product is protected twice over. A policy in migration 0008 makes the
database itself refuse a row belonging to another business, and the service's own ``WHERE tenant_id
= :id`` refuses it a second time. A forgotten filter is a bug; it is not a leak.

``tenants`` is the exception, ==on purpose==. It carries **no policy at all**: the admin resolves
its business by slug at boot, *before* any GUC can exist, so a policy there breaks the admin's boot
outright — and the public router makes slugs semi-public anyway. That decision is right, and this is
its price:

    ==The ``app`` role can SELECT every business's branding. Row-level security will not stop a
    query in this module from reading, or writing, the wrong one. The ``WHERE`` clause below is the
    entire guard.==

Which is why the queries here name the business EXPLICITLY, why nothing in this module has a
"current tenant" fallback, and why ``tests/rls/test_branding_isolation.py`` asserts *both* halves of
that sentence against a real PostgreSQL: that the policy is genuinely absent (so nobody later
mistakes RLS for the guard and relaxes this), and that a session bound to business B still cannot
obtain business A's brand through this service.

The failure mode being defended against is not exotic. A getter written as "fetch the tenant" — no
id, ``.first()``, or a ``session.get`` on a cached primary key — is *correct on a single-business
instance*, which is what a developer, a demo and every screenshot are. It would look perfect until
the second customer signed, and then it would put one clinic's logo on another clinic's page.

.. rubric:: The refusal is loud

There is no ``None`` return here. The ``tenant_id`` arrives from an authenticated request (whose API
key row holds a foreign key to ``tenants``) or from the admin's own resolved context, so "no such
business" does not mean "a guest typed a bad URL" — it means referential integrity is gone. Handing
back ``None`` invites a caller to render an unbranded page over a broken database and move on.

Transaction control (commit / rollback) belongs to the caller, as everywhere else in this layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.branding import (
    TenantBrandingRead,
    TenantBrandingUpdate,
    resolve_display_name,
)
from aethercal.server.db.models import Tenant


class BrandingServiceError(Exception):
    """Base class for branding-service failures."""


class TenantNotFoundError(BrandingServiceError):
    """No business with that id. A broken invariant, not a user error — see the module docstring."""


async def _require_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """The one query. ==The ``where`` is the belt== — ``tenants`` has no policy behind it."""
    tenant = (await session.scalars(select(Tenant).where(Tenant.id == tenant_id))).one_or_none()
    if tenant is None:
        raise TenantNotFoundError(f"no business with id {tenant_id}")
    return tenant


async def get_branding(session: AsyncSession, *, tenant_id: uuid.UUID) -> Tenant:
    """The branding row of ONE named business. Raises :class:`TenantNotFoundError` if it is gone.

    It returns the ORM row rather than a wire model because its two callers need two different
    projections of it: the API needs ``display_name`` RESOLVED (a guest's page must be handed the
    name it shows, not a rule to re-apply), while the admin's form needs the RAW ``public_name`` —
    an operator has to be able to see that they have not set one. :func:`public_branding` below is
    the API's projection, so the resolution rule is applied in exactly one place.
    """
    return await _require_tenant(session, tenant_id)


async def update_branding(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: TenantBrandingUpdate
) -> Tenant:
    """Replace the branding of ONE named business. ==A complete write, not a patch.==

    Every field is assigned, including the ``None``s: the admin form has a box for each one, and an
    emptied box means "remove it". A partial update would make "the operator cleared the logo" and
    "the operator did not mention the logo" the same request, and only one of them is what happened.

    The values arrive validated — ``TenantBrandingUpdate`` refuses a non-hex colour, a non-https
    logo and a string that is not an IANA zone at the edge (422). That is why nothing here re-checks
    them, and why nothing here owns a second copy of those rules.
    """
    tenant = await _require_tenant(session, tenant_id)
    tenant.public_name = data.public_name
    tenant.logo_url = data.logo_url
    tenant.accent_color = data.accent_color
    tenant.timezone = data.timezone
    return tenant


def public_branding(tenant: Tenant) -> TenantBrandingRead:
    """The guest-facing projection of ``tenant``: the resolved name, the logo, the colour, the zone.

    ==The one place ``public_name or name`` is decided.== The registered ``name``, the ``slug`` and
    the id are not on the wire model at all: a booking page needs none of them, and everything a
    public surface does not need is a thing that cannot leak from it.
    """
    return TenantBrandingRead(
        display_name=resolve_display_name(tenant.public_name, tenant.name),
        logo_url=tenant.logo_url,
        accent_color=tenant.accent_color,
        timezone=tenant.timezone,
    )


__all__ = [
    "BrandingServiceError",
    "TenantNotFoundError",
    "get_branding",
    "public_branding",
    "update_branding",
]
