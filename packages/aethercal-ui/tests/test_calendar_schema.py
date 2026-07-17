"""Drift guard for the cross-language calendar-props contract (AetherCal-06 §4, F2-D).

``calendar-props.schema.json`` is generated from the Python payload TypedDicts and committed into
the JS core package, where a vitest test validates the TypeScript payloads against it. This test
asserts the committed file still equals a fresh generation, so a change to a Python payload type
that was not re-generated (``uv run poe gen-schema``) fails CI instead of letting the two diverge.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from aethercal.ui.calendar import Calendar

_GENERATOR = Path(__file__).resolve().parents[1] / "scripts" / "gen_calendar_schema.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_calendar_schema", _GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_schema_matches_the_python_types() -> None:
    gen = _load_generator()
    committed = gen.SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == gen.render(), (
        "calendar-props.schema.json is stale relative to the Python payload types — "
        "run `uv run poe gen-schema` and commit the result."
    )


def test_schema_exists_at_the_path_vitest_imports() -> None:
    gen = _load_generator()
    assert gen.SCHEMA_PATH.name == "calendar-props.schema.json"
    assert gen.SCHEMA_PATH.exists()


def test_schema_events_are_all_wired_as_wrapper_event_triggers() -> None:
    # The candado that keeps the Reflex wrapper in sync with the cross-language contract: every
    # event the schema declares (including on_view_change / on_range_change, F2-NAV) must be a real
    # EventHandler trigger on the Calendar component — a schema event with no wrapper trigger (or a
    # renamed handler) is drift the contract must not allow.
    gen = _load_generator()
    schema_events = set(gen.build_schema()["events"])
    triggers = set(Calendar.get_event_triggers())
    missing = schema_events - triggers
    assert not missing, f"schema events without a wrapper trigger: {sorted(missing)}"
    assert {"on_view_change", "on_range_change"} <= schema_events
    assert {"on_view_change", "on_range_change"} <= triggers


def test_resource_timeline_shapes_are_in_the_contract() -> None:
    # RF-28: the timeline adds a resource dimension. The schema must carry the resource type itself
    # AND the `resourceId` that lets a drop / a range-select name the resource row it landed on —
    # otherwise the backend cannot tell WHICH host an event was dragged onto.
    gen = _load_generator()
    defs = gen.build_schema()["$defs"]

    resource = defs["CalendarResource"]
    assert set(resource["properties"]) == {"id", "title", "groupId", "color"}
    assert resource["required"] == ["id", "title"]  # groupId/color are optional

    # `resourceId` is OPTIONAL everywhere: the other four views have no resource dimension, and an
    # event may legitimately be unassigned.
    for name in ("CalendarEvent", "EventDropPayload", "RangeSelectPayload"):
        assert "resourceId" in defs[name]["properties"], name
        assert defs[name]["properties"]["resourceId"]["type"] == "string", name
        assert "resourceId" not in defs[name].get("required", []), name

    # A resize never changes the resource row, so it must NOT carry one (no misleading field).
    assert "resourceId" not in defs["EventResizePayload"]["properties"]


def test_context_menu_at_least_one_is_enforced_by_the_schema_not_the_flat_typeddict() -> None:
    # The Python TypedDict is intentionally permissive ({} is a valid dict); the "at least one of
    # id/start" refinement is imposed by the schema (minProperties: 1), which is what both languages
    # validate against. This test pins that enforcement so it can't silently disappear.
    gen = _load_generator()
    ctx = gen.build_schema()["$defs"]["ContextMenuPayload"]
    assert ctx["minProperties"] == 1
    assert "required" not in ctx  # no single field is required; at-least-one is the rule
    assert set(ctx["properties"]) == {"id", "start"}
