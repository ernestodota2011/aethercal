"""Server service layer: the async, session-bound operations behind the API.

Each service module owns one domain area and takes an ``AsyncSession`` as its first argument;
transaction control (commit/rollback) belongs to the caller (the ``get_session`` dependency for
requests, the CLI for admin commands), never to the service.
"""

from __future__ import annotations
