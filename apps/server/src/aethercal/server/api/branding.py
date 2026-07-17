"""``GET /api/v1/branding`` — the branding of the business the request authenticated as (RF-27).

The booking page is the consumer. It is a server-side HTTP client of this API and it presents ONE
key — the key of the business whose page it serves — so what this endpoint returns for that key IS
what appears at the top of that page. ==That makes this the seam criterion 44 is decided at.==

.. rubric:: The business is not an input, and that is the security property

There is no ``tenant_id`` in the path, no ``slug`` in the query string, no id in a body. The
business comes from the authenticated key, through the ``SECURITY DEFINER`` resolver, inside
``require_api_key`` — which is the same seam every other protected route in this product hangs from.
A caller therefore has nothing to *supply* in order to ask for somebody else's brand; there is no
parameter to tamper with, because there is no parameter.

That matters more here than it would elsewhere, because ``tenants`` is the one table with no
row-level-security policy behind it (0008, by design). Everywhere else a mistake in this layer would
be caught by the database. Here it would not — see :mod:`aethercal.server.services.branding`.

.. rubric:: Read-only, and keyless is B-04's job

Only ``GET``. The editor of branding is the ADMIN, which reaches the service in-process (and, once
B-02 lands, behind its role gates) — so putting a ``PATCH`` here would be a second write surface for
the same four columns, with its own idea of who may use it, before anybody had asked for one.

And the endpoint requires a key: this wave does not open a public router. B-04 is what makes the
booking page keyless and resolves the business from ``(tenant_slug, event_slug)`` in the ROUTE. When
it lands, it resolves a ``tenant_id`` and calls
:func:`~aethercal.server.services.branding.get_branding` with it — the same service, the same
``WHERE``, the same projection. Nothing here needs to change, and nothing here needs to be undone.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.branding import TenantBrandingRead
from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.deps import get_session
from aethercal.server.services.branding import get_branding, public_branding

router = APIRouter(prefix="/branding", tags=["branding"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[AuthContext, Depends(require_api_key)]


@router.get("", response_model=TenantBrandingRead)
async def retrieve(session: SessionDep, ctx: AuthDep) -> TenantBrandingRead:
    """The authenticated business's public name, logo, accent colour and timezone.

    No 404 branch: ``ctx.tenant_id`` came from an API key row whose ``tenant_id`` is a foreign key,
    so the business exists. If it somehow does not, the service raises rather than answering with an
    empty brand — a page rendered blank over a broken database is worse than a 500 that says so.
    """
    tenant = await get_branding(session, tenant_id=ctx.tenant_id)
    return public_branding(tenant)
