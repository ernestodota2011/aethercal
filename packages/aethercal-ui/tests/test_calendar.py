"""Tests for the Reflex wrapper around the AetherCal calendar core (F0-10 spike, Spike A).

Reflex components compile to a `Tag` describing the React element to render; we assert on that
compiled/rendered form directly rather than spinning up a browser, per the spike's scope (see
docs/spikes/f0-10-reflex-tsx.md). This proves two things: props declared in Python actually flow
into the rendered React props ("props in"), and the `on_event_drop` trigger is registered and
produces a real event chain when wired to a handler ("events out").
"""

from __future__ import annotations

import pytest
import reflex as rx

from aethercal.ui.calendar import Calendar


def test_calendar_compiles_to_the_aether_calendar_react_tag() -> None:
    component = Calendar.create()
    tag = component._render()
    assert tag.name == "AetherCalendar"


def test_calendar_is_not_a_global_scope_element() -> None:
    # NoSSRComponent + a local `library` — this must never render as a bare, unquoted
    # lowercase tag the way an intrinsic global element (e.g. "div") would.
    component = Calendar.create()
    assert component.library is not None
    assert component.tag == "AetherCalendar"


def test_calendar_library_points_at_the_packaged_local_bundle_not_npm() -> None:
    component = Calendar.create()
    # `rx.asset(..., shared=True).importable_path` always starts with "$/public/" — the
    # marker that this is a local asset shipped by the package, never an npm/CDN specifier.
    assert component.library is not None
    assert component.library.startswith("$/public/")
    assert component.library.endswith("aethercal-calendar.js")


def test_calendar_is_a_no_ssr_component() -> None:
    # Drag-and-drop needs real browser DOM APIs; this must never attempt server-side rendering.
    assert isinstance(Calendar.create(), rx.NoSSRComponent)


def test_default_view_prop_flows_into_the_rendered_props() -> None:
    component = Calendar.create()
    tag = component._render()
    assert "view" in tag.props
    assert str(tag.props["view"]) == '"month"'


def test_explicit_view_prop_flows_into_the_rendered_props() -> None:
    component = Calendar.create(view="week")
    tag = component._render()
    assert str(tag.props["view"]) == '"week"'


def test_events_prop_flows_into_the_rendered_props() -> None:
    events = [
        {
            "id": "evt-1",
            "title": "Consult",
            "start": "2026-07-09T14:00:00",
            "end": "2026-07-09T14:30:00",
        }
    ]
    component = Calendar.create(events=events)
    tag = component._render()
    assert "events" in tag.props
    rendered_events = str(tag.props["events"])
    assert "evt-1" in rendered_events
    assert "Consult" in rendered_events


def test_day_and_list_views_are_accepted_contract_values() -> None:
    # F2-A only renders `month`, but the contract (AetherCal-06 §5) is 4 views; `day`/`list`
    # must be accepted so F2-B/C can render them without touching the wrapper.
    for view in ("month", "week", "day", "list"):
        component = Calendar.create(view=view)
        assert str(component._render().props["view"]) == f'"{view}"'


def test_invalid_view_literal_raises() -> None:
    with pytest.raises(ValueError, match=r"Calendar\.view must be one of"):
        Calendar.create(view="year")


def test_default_locale_flows_into_the_rendered_props() -> None:
    tag = Calendar.create()._render()
    assert "locale" in tag.props
    assert str(tag.props["locale"]) == '"en"'


def test_explicit_locale_flows_into_the_rendered_props() -> None:
    tag = Calendar.create(locale="es")._render()
    assert str(tag.props["locale"]) == '"es"'


def test_first_day_of_week_flows_into_the_rendered_props_camelcased() -> None:
    tag = Calendar.create(first_day_of_week=0)._render()
    # Reflex camel-cases Python prop names for the JSX tag; the bundle reads `firstDayOfWeek`.
    assert "firstDayOfWeek" in tag.props
    assert str(tag.props["firstDayOfWeek"]) == "0"


def test_first_day_of_week_boundary_values_are_accepted() -> None:
    for good in (0, 1, 6):
        assert str(
            Calendar.create(first_day_of_week=good)._render().props["firstDayOfWeek"]
        ) == str(good)


def test_invalid_first_day_of_week_literal_raises() -> None:
    # bool is an int subclass; True/False must be rejected too (they are not a valid weekday int).
    for bad in (-1, 7, 13, True, False):
        with pytest.raises(ValueError, match=r"first_day_of_week must be 0..6"):
            Calendar.create(first_day_of_week=bad)


def test_on_event_drop_is_a_registered_event_trigger() -> None:
    assert "on_event_drop" in Calendar.get_event_triggers()


def test_on_event_drop_handler_is_wired_into_the_rendered_props() -> None:
    component = Calendar.create(on_event_drop=rx.console_log("dropped"))
    tag = component._render()
    # Reflex camel-cases Python prop names for the JSX tag.
    assert "onEventDrop" in tag.props


def test_calendar_without_an_event_drop_handler_omits_the_prop() -> None:
    component = Calendar.create()
    tag = component._render()
    assert "onEventDrop" not in tag.props
