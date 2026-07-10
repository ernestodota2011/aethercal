"""Offline unit tests for the slots query-window cap (F1-04, DoS guard).

The ``GET /slots`` handler bounds the ``from``..``to`` window so a single request can never ask the
service to materialize an unbounded date range. The cap check is a pure, DB-free validator over two
dates, so it is exercised here directly (the HTTP-level 422 contract lives in the db-marked
``test_slots_api.py``). It rejects an over-cap window with the same clean 422 envelope the endpoint
uses everywhere else.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException, status

from aethercal.server.api.slots import MAX_QUERY_DAYS, _require_window_within_cap


def test_window_exactly_at_cap_is_allowed() -> None:
    base = date(2026, 7, 6)
    # A window whose span equals the cap is fine (the cap is inclusive); no exception is raised.
    _require_window_within_cap(base, base + timedelta(days=MAX_QUERY_DAYS))


def test_window_one_day_over_cap_is_rejected_422() -> None:
    base = date(2026, 7, 6)
    with pytest.raises(HTTPException) as exc_info:
        _require_window_within_cap(base, base + timedelta(days=MAX_QUERY_DAYS + 1))

    exc = exc_info.value
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "window_too_large"


def test_max_query_days_is_a_sane_booking_bound() -> None:
    # A sensible cap for a booking calendar: at least a month, at most a season of lookahead.
    assert 28 <= MAX_QUERY_DAYS <= 92
