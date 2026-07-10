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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped :class:`AsyncSession`; commit on success, roll back on error."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    session = sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
