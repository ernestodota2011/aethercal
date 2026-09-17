"""Microsoft 365 / Graph API integration for AetherCal (Horizon 1).

Pure transforms, client transport, and schedule queries for Microsoft 365 Exchange/Calendar.
"""

from __future__ import annotations

from .client import (
    MICROSOFT_HTTP_TIMEOUT_SECONDS,
    build_service,
    delete_event,
    insert_event,
    query_busy,
)
from .parse import (
    MicrosoftEventRequest,
    build_graph_event_body,
    build_schedule_request_body,
    extract_teams_join_url,
    parse_graph_schedule,
)

__all__ = [
    "MICROSOFT_HTTP_TIMEOUT_SECONDS",
    "MicrosoftEventRequest",
    "build_graph_event_body",
    "build_schedule_request_body",
    "build_service",
    "delete_event",
    "extract_teams_join_url",
    "insert_event",
    "parse_graph_schedule",
    "query_busy",
]
