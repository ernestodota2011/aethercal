"""Generate ``calendar-props.schema.json`` from the Python payload TypedDicts.

The schema is the CROSS-LANGUAGE CONTRACT for the calendar's event payloads (AetherCal-06 §4). It is
GENERATED from the single source of truth — the ``aethercal.ui.calendar`` TypedDicts the Reflex
wrapper declares — and committed into the JS core package (``js/packages/core/src``), where a vitest
test validates the TypeScript payloads against it. A Python drift test re-runs this generator and
fails if the committed file is stale, so the Python and TypeScript sides can never silently diverge
(the anti-drift lock).

Run ``uv run poe gen-schema`` to regenerate after changing a payload type.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NotRequired, Required, get_args, get_origin, get_type_hints

# Importing the wrapper resolves ``rx.asset`` at module scope, which in a real Reflex app symlinks
# the bundle into the cwd; this repo root is not one, so skip that side effect.
os.environ.setdefault("REFLEX_BACKEND_ONLY", "1")

from aethercal.ui.calendar import (
    CalendarEvent,
    CalendarResource,
    ContextMenuPayload,
    EventClickPayload,
    EventDropPayload,
    EventResizePayload,
    RangeSelectPayload,
    ViewChangePayload,
)

# The committed schema lives beside the TS types it locks, so vitest imports it directly.
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "js"
    / "packages"
    / "core"
    / "src"
    / "calendar-props.schema.json"
)

_SCALARS: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

# The outbound events (§4) and the payload type each carries.
_EVENTS: dict[str, str] = {
    "on_event_drop": "EventDropPayload",
    "on_event_resize": "EventResizePayload",
    "on_range_select": "RangeSelectPayload",
    "on_event_click": "EventClickPayload",
    "on_context_menu": "ContextMenuPayload",
    "on_view_change": "ViewChangePayload",
    "on_range_change": "ViewChangePayload",
}

_REVISION_SEMANTICS = (
    "revision is a per-event monotonic-increasing integer the server assigns on each accepted "
    "mutation. The client applies a response only if its revision is greater than the highest "
    "already applied for that event; a stale/out-of-order response (revision <= applied) is "
    "discarded. A pending mutation with no response within the client budget is rolled back, so "
    "the UI never sticks in pending."
)


def _is_typeddict(tp: Any) -> bool:
    return hasattr(tp, "__required_keys__") and hasattr(tp, "__annotations__")


def _json_type(tp: Any) -> dict[str, Any]:
    origin = get_origin(tp)
    if origin is list:
        (item,) = get_args(tp)
        return {"type": "array", "items": _json_type(item)}
    if tp in _SCALARS:
        return {"type": _SCALARS[tp]}
    if _is_typeddict(tp):
        return {"$ref": f"#/$defs/{tp.__name__}"}
    msg = f"unsupported payload field type: {tp!r}"
    raise TypeError(msg)


def _schema_for(td: Any) -> dict[str, Any]:
    # ``__required_keys__`` is unreliable under ``from __future__ import annotations`` (stringized
    # NotRequired is not resolved at class creation), so detect optionality from the resolved hints.
    hints = get_type_hints(td, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, hint in hints.items():
        origin = get_origin(hint)
        if origin is NotRequired:
            (inner,) = get_args(hint)
            properties[name] = _json_type(inner)
        elif origin is Required:
            (inner,) = get_args(hint)
            properties[name] = _json_type(inner)
            required.append(name)
        else:
            properties[name] = _json_type(hint)
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = sorted(required)
    else:
        # An all-optional payload (e.g. ContextMenuPayload: id? / start?) must still carry at least
        # one field — the empty object is never a valid event payload. The TS side models the same
        # invariant as an at-least-one union.
        schema["minProperties"] = 1
    return schema


# The payload types, in the order they appear in the schema's ``$defs``. ``CalendarResource`` is a
# PROP type rather than an event payload (like ``CalendarEvent``), but it crosses the same
# Python↔TS boundary, so it is locked by the same contract.
_PAYLOAD_TYPES: tuple[Any, ...] = (
    CalendarEvent,
    CalendarResource,
    EventDropPayload,
    EventResizePayload,
    RangeSelectPayload,
    EventClickPayload,
    ContextMenuPayload,
    ViewChangePayload,
)


def build_schema() -> dict[str, Any]:
    """Build the full JSON Schema (draft 2020-12) for the calendar props/events contract."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://aethercal.dev/calendar-props.schema.json",
        "title": "AetherCal calendar-props contract",
        "description": (
            "Generated from the Python payload TypedDicts in aethercal.ui.calendar (single source "
            "of truth). Validated against the TypeScript types in vitest as the anti-drift lock. "
            "Do not edit by hand - run `uv run poe gen-schema`."
        ),
        "revisionSemantics": _REVISION_SEMANTICS,
        "events": {name: {"$ref": f"#/$defs/{ref}"} for name, ref in _EVENTS.items()},
        "$defs": {td.__name__: _schema_for(td) for td in _PAYLOAD_TYPES},
    }


def render() -> str:
    """Return the exact committed text (sorted keys + trailing newline) for byte-for-byte drift."""
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    """Write the generated schema to its committed path."""
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
