"""FastAPI request dependencies.

``get_session`` is the single seam every service reaches the database through: it opens a session
from the app's ``sessionmaker``, commits when the handler returns cleanly, rolls back if it raises,
and always closes. Because the sessionmaker lives on ``app.state`` (set eagerly in ``create_app``),
this works under an ASGITransport test client without the lifespan ever running.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from aethercal.server.db.guc import request_scope


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped :class:`AsyncSession`; commit on success, roll back on error.

    ==It also opens the request's TENANT SCOPE, and that is not decoration.==

    The scope starts EMPTY: nothing bound, so — under row-level security — nothing visible. The auth
    dependency (or the guest-token path) resolves the business and binds it, and every transaction
    this session opens from then on carries it, stamped by the ``after_begin`` listener. On the way
    out, the binding is torn down, whatever happened in between.

    Declaring the scope explicitly, rather than leaning on "each request is its own asyncio
    Task", is deliberate — see :func:`~aethercal.server.db.guc.request_scope`. Under uvicorn that
    assumption
    happens to hold; under an ASGI app called directly (an ``httpx.ASGITransport``, an ASGI
    middleware, a background task) it does not — and a binding that survives from one request into
    the next is a request reading another business's rows.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    with request_scope():
        session = sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
