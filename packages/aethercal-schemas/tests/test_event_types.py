"""Validation tests for the EventType API schemas (RF-14).

These pin the bounds (durations as integer seconds, mirroring the DB columns) that the API contract
promises before any service or router touches them.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aethercal.schemas.event_types import (
    SUPPORTED_TRANSLATION_LOCALES,
    EventTypeCreate,
    EventTypeRead,
    EventTypeUpdate,
    resolve_description,
    resolve_title,
)


def _valid_create_kwargs() -> dict[str, object]:
    return {
        "host_id": uuid.uuid4(),
        "schedule_id": uuid.uuid4(),
        "slug": "intro-call",
        "title": "Intro Call",
        "duration_seconds": 1800,
        "max_advance_seconds": 60 * 60 * 24 * 30,
    }


def test_create_accepts_minimal_valid_payload_with_defaults() -> None:
    model = EventTypeCreate(**_valid_create_kwargs())

    assert model.slug == "intro-call"
    assert model.duration_seconds == 1800
    # Optional-with-default fields fall back to safe zeros / empties / active.
    assert model.buffer_before_seconds == 0
    assert model.buffer_after_seconds == 0
    assert model.min_notice_seconds == 0
    assert model.increment_seconds is None
    assert model.max_per_day is None
    assert model.description is None
    assert model.location is None
    assert model.questions == []
    assert model.active is True


def test_create_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "duration_seconds": 0})


def test_create_rejects_negative_buffers_and_notice() -> None:
    for field in ("buffer_before_seconds", "buffer_after_seconds", "min_notice_seconds"):
        with pytest.raises(ValidationError):
            EventTypeCreate(**{**_valid_create_kwargs(), field: -1})


def test_create_rejects_non_positive_max_advance() -> None:
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "max_advance_seconds": 0})


def test_create_rejects_non_positive_increment_when_set() -> None:
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "increment_seconds": 0})


def test_create_rejects_max_per_day_below_one_when_set() -> None:
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "max_per_day": 0})


def test_create_rejects_blank_slug_and_title() -> None:
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "slug": ""})
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "title": ""})


def test_create_translations_default_to_empty_dict() -> None:
    model = EventTypeCreate(**_valid_create_kwargs())

    assert model.title_translations == {}
    assert model.description_translations == {}


def test_create_accepts_allowlisted_locale_overrides() -> None:
    model = EventTypeCreate(
        **{
            **_valid_create_kwargs(),
            "title_translations": {"en": "Discovery call"},
            "description_translations": {"en": "A quick discovery call"},
        }
    )

    assert model.title_translations == {"en": "Discovery call"}
    assert model.description_translations == {"en": "A quick discovery call"}


def test_create_rejects_locale_outside_allowlist_in_title_translations() -> None:
    assert "fr" not in SUPPORTED_TRANSLATION_LOCALES
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "title_translations": {"fr": "Appel"}})


def test_create_rejects_locale_outside_allowlist_in_description_translations() -> None:
    assert "zz" not in SUPPORTED_TRANSLATION_LOCALES
    with pytest.raises(ValidationError):
        EventTypeCreate(**{**_valid_create_kwargs(), "description_translations": {"zz": "nope"}})


def test_update_is_fully_optional_and_only_sets_provided_fields() -> None:
    empty = EventTypeUpdate()
    assert empty.model_dump(exclude_unset=True) == {}

    partial = EventTypeUpdate(title="Renamed", active=False)
    assert partial.model_dump(exclude_unset=True) == {"title": "Renamed", "active": False}


def test_update_still_enforces_bounds_when_a_field_is_provided() -> None:
    with pytest.raises(ValidationError):
        EventTypeUpdate(duration_seconds=0)
    with pytest.raises(ValidationError):
        EventTypeUpdate(increment_seconds=0)
    with pytest.raises(ValidationError):
        EventTypeUpdate(max_per_day=0)


def test_update_translations_are_unset_by_default() -> None:
    empty = EventTypeUpdate()
    assert "title_translations" not in empty.model_dump(exclude_unset=True)
    assert "description_translations" not in empty.model_dump(exclude_unset=True)


def test_update_accepts_allowlisted_locale_overrides() -> None:
    model = EventTypeUpdate(title_translations={"en": "Renamed EN"})
    assert model.model_dump(exclude_unset=True) == {"title_translations": {"en": "Renamed EN"}}


def test_update_rejects_locale_outside_allowlist() -> None:
    with pytest.raises(ValidationError):
        EventTypeUpdate(title_translations={"fr": "nope"})
    with pytest.raises(ValidationError):
        EventTypeUpdate(description_translations={"zz": "nope"})


def test_read_builds_from_orm_like_attributes() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="intro-call",
        title="Intro Call",
        description=None,
        location=None,
        duration_seconds=1800,
        buffer_before_seconds=0,
        buffer_after_seconds=0,
        min_notice_seconds=0,
        max_advance_seconds=2_592_000,
        increment_seconds=None,
        max_per_day=None,
        questions=[],
        active=True,
    )

    read = EventTypeRead.model_validate(row)
    assert read.id == row.id
    assert read.slug == "intro-call"
    assert read.active is True
    # No translations were set on the row — default to empty maps, never a missing attribute error.
    assert read.title_translations == {}
    assert read.description_translations == {}


def test_read_returns_translation_maps_when_present() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="intro-call",
        title="Intro Call",
        description="Canonical description",
        location=None,
        duration_seconds=1800,
        buffer_before_seconds=0,
        buffer_after_seconds=0,
        min_notice_seconds=0,
        max_advance_seconds=2_592_000,
        increment_seconds=None,
        max_per_day=None,
        questions=[],
        active=True,
        title_translations={"en": "Discovery call"},
        description_translations={"en": "A quick discovery call"},
    )

    read = EventTypeRead.model_validate(row)
    assert read.title_translations == {"en": "Discovery call"}
    assert read.description_translations == {"en": "A quick discovery call"}


def _read_kwargs(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "host_id": uuid.uuid4(),
        "schedule_id": uuid.uuid4(),
        "slug": "intro-call",
        "title": "Intro Call",
        "description": "Canonical description",
        "location": None,
        "duration_seconds": 1800,
        "buffer_before_seconds": 0,
        "buffer_after_seconds": 0,
        "min_notice_seconds": 0,
        "max_advance_seconds": 2_592_000,
        "increment_seconds": None,
        "max_per_day": None,
        "questions": [],
        "active": True,
    }
    data.update(overrides)
    return data


def test_resolve_title_returns_locale_override_when_present() -> None:
    event = EventTypeRead(**_read_kwargs(title_translations={"en": "Discovery call"}))
    assert resolve_title(event, "en") == "Discovery call"


def test_resolve_title_falls_back_to_canonical_when_no_override() -> None:
    event = EventTypeRead(**_read_kwargs())
    assert resolve_title(event, "en") == "Intro Call"


def test_resolve_title_falls_back_to_canonical_when_override_is_empty_string() -> None:
    event = EventTypeRead(**_read_kwargs(title_translations={"en": ""}))
    assert resolve_title(event, "en") == "Intro Call"


def test_resolve_description_returns_locale_override_when_present() -> None:
    event = EventTypeRead(**_read_kwargs(description_translations={"en": "Quick chat"}))
    assert resolve_description(event, "en") == "Quick chat"


def test_resolve_description_falls_back_to_canonical_when_no_override() -> None:
    event = EventTypeRead(**_read_kwargs(description="Canonical desc"))
    assert resolve_description(event, "en") == "Canonical desc"


def test_resolve_description_falls_back_to_canonical_when_override_is_empty_string() -> None:
    event = EventTypeRead(
        **_read_kwargs(description="Canonical desc", description_translations={"en": ""})
    )
    assert resolve_description(event, "en") == "Canonical desc"


def test_resolve_description_returns_none_when_neither_override_nor_canonical() -> None:
    event = EventTypeRead(**_read_kwargs(description=None))
    assert resolve_description(event, "en") is None
