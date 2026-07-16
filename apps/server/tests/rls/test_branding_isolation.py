"""==Criterion 44==: a business's brand appears on ITS public page and on nobody else's.

Against a real PostgreSQL, under the real belt, with two real businesses — because on a database
with one row in ``tenants`` this whole feature is indistinguishable from a broken one.

.. rubric:: What makes this criterion different from every other isolation test in this directory

Everywhere else, the belt is worn twice: migration 0008 puts ``FORCE ROW LEVEL SECURITY`` and a
policy on every table carrying a ``tenant_id``, *and* the service filters by it as well. A service
that forgets its ``WHERE`` still reads zero rows, and the test that catches it goes red loudly.

``tenants`` has **no policy**, on purpose (the admin reads it by slug before any GUC can exist; the
public router makes slugs semi-public). So branding is protected once — by the ``WHERE tenants.id =
:id`` in ``services/branding.py`` — and these tests assert BOTH halves of that sentence:

* :class:`TestTheDatabaseIsNotTheGuardHere` proves the app role really can read every business's
  branding row, so that nobody reads the code, assumes RLS has this covered, and deletes the filter;
* the rest prove that, that being so, the service and the API are exact anyway.

The first class is not a redundant restatement of ``test_the_tenant_root_deliberately_carries_no_
policy``. That one asserts a schema fact. This one asserts what the schema fact MEANS for the query
this wave adds: the leak is one forgotten clause away, and nothing beneath it would catch it.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.schemas.branding import TenantBrandingUpdate
from aethercal.server.db.guc import bind_tenant
from aethercal.server.db.models import Tenant
from aethercal.server.services.branding import get_branding, update_branding

pytestmark = pytest.mark.db

BRANDING = "/api/v1/branding"

SOL = TenantBrandingUpdate(
    public_name="Clinica Sol",
    logo_url="https://cdn.sol.example/sol.png",
    accent_color="#e0894b",
    timezone="America/New_York",
)
LUNA = TenantBrandingUpdate(
    public_name="Estudio Luna",
    logo_url="https://cdn.luna.example/luna.svg",
    accent_color="#3a5f8f",
    timezone="Europe/Madrid",
)


async def _brand_the_two(
    owner_maker: async_sessionmaker[AsyncSession],
    businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """Give business 0 the Sol brand and business 1 the Luna brand. ==Seeded as the OWNER.==

    Arranging TWO businesses cannot be done on the app engine — ``bind_tenant`` refuses to re-bind
    a scope to a second one — which is exactly why the harness has an owner engine at all.
    """
    (sol_id, _), (luna_id, _) = businesses
    async with owner_maker() as session, session.begin():
        await update_branding(session, tenant_id=sol_id, data=SOL)
        await update_branding(session, tenant_id=luna_id, data=LUNA)


class TestTheDatabaseIsNotTheGuardHere:
    """==Read this before touching ``services/branding.py``.==

    ``tenants`` has no policy. These two tests exist to make that concrete, so the ``WHERE`` clause
    in the branding service is never mistaken for belt-and-braces and quietly removed.
    """

    async def test_the_app_role_can_read_every_businesss_branding_row(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """An unfiltered ``SELECT`` on the app role, bound to ONE business, returns BOTH rows.

        On any other table this would return one row (or zero). Here it returns everything — which
        is precisely the hazard the service's filter exists to answer, and the reason it is not
        optional.
        """
        await _brand_the_two(owner_maker, two_businesses)
        (sol_id, _), _ = two_businesses

        async with app_maker() as session, session.begin():
            await bind_tenant(session, sol_id)
            names = set((await session.scalars(sa.select(Tenant.public_name))).all())

        assert names == {"Clinica Sol", "Estudio Luna"}

    async def test_a_forgotten_where_clause_would_therefore_leak(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """The exact bug, written out: "fetch the tenant" instead of ``.where(id == tenant_id)``.

        Bound to business B, the naive query hands back business A's row. It is here as an
        executable statement of the failure mode — a demo instance with one business would never
        show it, and that is how it would ship.

        The ``order_by`` is the fixture's slug (``biz-0`` < ``biz-1``) purely so the wrong answer is
        DETERMINISTIC and this test says something. It is not the point. The point is that no
        ordering, and no ``LIMIT``, can be *right*: with no policy on ``tenants``, an unfiltered
        query is choosing between two businesses' rows on a criterion that has nothing to do with
        which business is asking.
        """
        await _brand_the_two(owner_maker, two_businesses)
        (sol_id, _), (luna_id, _) = two_businesses

        async with app_maker() as session, session.begin():
            await bind_tenant(session, luna_id)
            naive = (await session.scalars(sa.select(Tenant).order_by(Tenant.slug))).first()
            correct = await get_branding(session, tenant_id=luna_id)

            assert naive is not None
            assert naive.id == sol_id, "the naive query reached the OTHER business"
            assert correct.id == luna_id
            assert correct.public_name == "Estudio Luna"


class TestTheServiceIsExactAnyway:
    """Bound to B, under the real belt, the service still returns B — and only B."""

    async def test_a_session_bound_to_b_gets_bs_brand(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        await _brand_the_two(owner_maker, two_businesses)
        (sol_id, _), (luna_id, _) = two_businesses

        async with app_maker() as session, session.begin():
            await bind_tenant(session, luna_id)
            brand = await get_branding(session, tenant_id=luna_id)

            assert brand.public_name == "Estudio Luna"
            assert brand.logo_url == "https://cdn.luna.example/luna.svg"
            assert brand.accent_color == "#3a5f8f"
            assert brand.timezone == "Europe/Madrid"
            assert brand.id != sol_id


class TestCriterion44OverTheWire:
    """==The criterion itself.== Two businesses, two API keys, two brands, one instance.

    The booking page is a client of this endpoint and holds ONE key — its business's. So "the brand
    served to A's page" IS "the brand this endpoint returns for A's key", and this is where the
    criterion is decided. The business is never taken from the request body or a query parameter: it
    comes from the authenticated key, through the ``SECURITY DEFINER`` resolver, in
    ``require_api_key``. There is no input a caller could supply to ask for somebody else's.
    """

    async def test_each_key_gets_its_own_brand_and_never_the_others(
        self,
        client: AsyncClient,
        owner_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        await _brand_the_two(owner_maker, two_businesses)
        (_, sol_key), (_, luna_key) = two_businesses

        sol = await client.get(BRANDING, headers={"Authorization": f"Bearer {sol_key}"})
        luna = await client.get(BRANDING, headers={"Authorization": f"Bearer {luna_key}"})

        assert sol.status_code == 200
        assert luna.status_code == 200

        sol_body, luna_body = sol.json(), luna.json()
        assert sol_body == {
            "display_name": "Clinica Sol",
            "logo_url": "https://cdn.sol.example/sol.png",
            "accent_color": "#e0894b",
            "timezone": "America/New_York",
        }
        assert luna_body == {
            "display_name": "Estudio Luna",
            "logo_url": "https://cdn.luna.example/luna.svg",
            "accent_color": "#3a5f8f",
            "timezone": "Europe/Madrid",
        }

        # Stated the other way round as well, because the assertions above would still pass if the
        # endpoint returned BOTH businesses' values merged into one object.
        for field, value in luna_body.items():
            assert sol_body[field] != value, f"business A's page carries B's {field}"

    async def test_branding_needs_a_key(self, client: AsyncClient) -> None:
        """Not a public endpoint in this wave. The keyless public router is B-04's (see the API
        module's docstring): until it lands, the booking page presents its business's key."""
        resp = await client.get(BRANDING)
        assert resp.status_code == 401

    async def test_an_unbranded_business_falls_back_to_its_registered_name(
        self, client: AsyncClient, two_businesses: list[tuple[uuid.UUID, str]]
    ) -> None:
        """Nothing seeded: the fallback is the row's ``name``, and the zone is the 0014 default."""
        (_, sol_key), _ = two_businesses

        resp = await client.get(BRANDING, headers={"Authorization": f"Bearer {sol_key}"})

        assert resp.status_code == 200
        assert resp.json() == {
            "display_name": "Business 0",
            "logo_url": None,
            "accent_color": None,
            "timezone": "UTC",
        }
