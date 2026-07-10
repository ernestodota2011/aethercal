"""Tests for the shared API base schemas: the error envelope and the pagination page."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from aethercal.schemas import ErrorResponse, Page


def test_error_response_carries_code_and_message() -> None:
    error = ErrorResponse(error="unauthorized", message="Invalid or missing API key")
    assert error.error == "unauthorized"
    assert error.message == "Invalid or missing API key"
    assert error.model_dump() == {
        "error": "unauthorized",
        "message": "Invalid or missing API key",
    }


def test_error_response_requires_both_fields() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse(error="oops")  # type: ignore[call-arg]


def test_page_holds_items_and_pagination_metadata() -> None:
    page: Page[int] = Page(items=[1, 2, 3], total=3, limit=10, offset=0)
    assert page.items == [1, 2, 3]
    assert page.total == 3
    assert page.limit == 10
    assert page.offset == 0


def test_page_is_generic_over_the_item_model() -> None:
    class Row(BaseModel):
        value: str

    page: Page[Row] = Page(items=[Row(value="a")], total=1, limit=25, offset=0)
    assert page.items[0].value == "a"
    assert page.model_dump() == {
        "items": [{"value": "a"}],
        "total": 1,
        "limit": 25,
        "offset": 0,
    }


def test_page_validates_item_type() -> None:
    with pytest.raises(ValidationError):
        Page[int](items=["not-an-int"], total=1, limit=10, offset=0)
