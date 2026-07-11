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


def test_context_menu_at_least_one_is_enforced_by_the_schema_not_the_flat_typeddict() -> None:
    # The Python TypedDict is intentionally permissive ({} is a valid dict); the "at least one of
    # id/start" refinement is imposed by the schema (minProperties: 1), which is what both languages
    # validate against. This test pins that enforcement so it can't silently disappear.
    gen = _load_generator()
    ctx = gen.build_schema()["$defs"]["ContextMenuPayload"]
    assert ctx["minProperties"] == 1
    assert "required" not in ctx  # no single field is required; at-least-one is the rule
    assert set(ctx["properties"]) == {"id", "start"}
