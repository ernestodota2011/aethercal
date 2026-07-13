"""Reflex wrapper around the AetherCal calendar (a custom React component over a headless core).

The JS side is a pnpm workspace at ``packages/aethercal-ui/js`` split into two packages
(AetherCal-06 §3): ``@aethercal/calendar-core`` (headless, TS-pure geometry + state machines) and
``@aethercal/calendar-react`` (the rendering layer). esbuild bundles the React layer — keeping
``react``/``react/jsx-runtime`` external — into a single committed, reproducible artifact
(``assets/aethercal-calendar.js``; ``pnpm build`` regenerates it) that ships *inside this
package's wheel* (see ``[tool.hatch.build.targets.wheel].artifacts`` in
``packages/aethercal-ui/pyproject.toml``). This module wraps that bundle as a real ``rx.Component``
with typed props flowing in and a real event trigger flowing out.

F2-A scope: the React layer renders the production **month** view; ``week``/``day``/``list`` are
valid contract values (§4) but render an honest "not available yet" placeholder until F2-B/C. The
React-dedupe risk was closed in ``docs/spikes/f2-dr-react-dedupe.md`` (GO — a single React
instance, no "Invalid hook call").

Reflex's public ``reflex``/``rx`` namespace re-exports ``Component``, ``NoSSRComponent``, ``Var``,
``EventHandler`` and the ``event_spec`` helpers, but *not* the ``field()`` used to declare a
component prop with a docstring — that only exists on the internal ``reflex_base`` package (the
same one every bundled Reflex add-on component, e.g. ``reflex_components_react_player``, imports
from). This module follows that same convention; see the decision doc for why that's a real
version-fragility risk worth flagging rather than hiding (the smoke test that guards it hardens in
F2-H).

The whole ``reflex``/``reflex_base`` boundary is untyped from pyright's point of view (no
``py.typed`` marker for either at strict-check time), so every reflex-facing symbol is imported
explicitly and re-typed at the boundary — nothing "Any"-shaped leaks into this module's own
public surface.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import NotRequired, TypedDict

import reflex as rx
from reflex.vars import ObjectVar
from reflex_base.components.component import field as rx_field

from aethercal.ui.theme import PRESET_NAMES

_BUNDLE = rx.asset(path="assets/aethercal-calendar.js", shared=True)

# The calendar surfaces (AetherCal-06 §5) — the four original views plus the RF-28 resource
# ``timeline`` (resources in rows, time on the horizontal axis). Kept in sync with the TS
# ``CalendarView`` union (js/packages/core/src/types.ts).
_VALID_VIEWS = frozenset({"month", "week", "day", "list", "timeline"})

# How many days the timeline's horizontal axis may span (RF-28). Bounded on both ends: a 0-day
# window has nothing to render, and an unbounded one would build an unusably dense axis. The React
# layer clamps into this same range, so a dynamic ``Var`` degrades instead of breaking the axis.
_MIN_TIMELINE_DAYS = 1
_MAX_TIMELINE_DAYS = 31

# The four theme presets (F2-E, AetherCal-06 §7). A literal ``theme`` string must be one of these;
# a dict of ``--ac-*`` token overrides (e.g. ``Theme.dark().to_css_vars()``) is also accepted, and a
# dynamic ``Var`` is not literal-checked (its value is a frontend concern the React layer resolves).
_VALID_THEMES = frozenset(PRESET_NAMES)

# A calendar anchor is a naive-local ISO date ("2026-07-15") or datetime ("2026-07-15T09:00:00"),
# matching the React layer's `parseLocalDateTime`. Blank means "today" (resolved client-side); a
# non-blank literal must be well-formed AND a real calendar date (so "2026-13-01" is rejected).
_ANCHOR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")


def _is_valid_anchor(value: str) -> bool:
    """Whether ``value`` is a well-formed, real naive-local ISO date/datetime anchor.

    Validates the WHOLE value, not just the date: a date+time is checked with
    ``datetime.fromisoformat`` (which range-checks the hours/minutes/seconds, rejecting e.g.
    ``25:00:00`` or ``12:60:00``), a bare date with ``date.fromisoformat``.
    """
    if not _ANCHOR_RE.match(value):
        return False
    normalized = value.replace(" ", "T")
    try:
        if "T" in normalized:
            datetime.fromisoformat(normalized)
        else:
            date.fromisoformat(normalized)
    except ValueError:
        return False
    return True


class CalendarResource(TypedDict):
    """One row of the RF-28 resource timeline, matching the JS ``CalendarResource`` type.

    Deliberately GENERIC: the component knows nothing about what a resource *is*. AetherCal's
    backend maps resource → host, but the component takes an arbitrary array so the same timeline
    serves rooms, chairs, or machines without a code change.

    ``groupId`` is both the grouping KEY and the group's display LABEL (there is no separate title
    field): a collapsible group is exactly "the resources that share this string", so a host passes
    a human-readable value ("Clinic A") and gets a human-readable header for free.
    """

    id: str
    title: str
    # `groupId` is camelCase to match the JS prop the bundle reads (js/packages/core types).
    groupId: NotRequired[str]
    color: NotRequired[str]


class CalendarEvent(TypedDict):
    """One calendar event, matching the JS ``CalendarEvent`` type (calendar-core types.ts)."""

    id: str
    title: str
    start: str  # ISO 8601, naive local wall-time, e.g. "2026-07-09T14:00:00".
    end: str
    # `allDay` is camelCase to match the JS prop the bundle reads (js/packages/core types).
    allDay: NotRequired[bool]
    color: NotRequired[str]
    editable: NotRequired[bool]
    # Monotonic-increasing per-event integer, server-assigned (§4). Optional in F2-A; the
    # reconciliation that makes it load-bearing is F2-D.
    revision: NotRequired[int]
    # Which timeline resource row this event belongs to (RF-28). Optional: the other four views
    # have no resource dimension, and an event may legitimately be unassigned (the timeline
    # surfaces those in their own row rather than silently dropping them).
    resourceId: NotRequired[str]


class EventDropPayload(TypedDict):
    """The payload the JS core sends back on ``on_event_drop`` (js/packages/core/src/types.ts).

    ``client_mutation_id`` is set by the F2-D reconciliation layer so the server can dedupe a
    retried mutation idempotently; ``revision`` is echoed from the dragged event.

    ``resourceId`` names the TARGET resource row when the drop happened on the timeline (RF-28) —
    the whole point of dragging between rows is that the backend learns which host the event was
    moved to. It is absent for the month/week/day views, which have no resource dimension.
    """

    id: str
    start: str
    end: str
    revision: NotRequired[int]
    client_mutation_id: NotRequired[str]
    resourceId: NotRequired[str]


class EventResizePayload(TypedDict):
    """The payload for ``on_event_resize`` — a duration change (one endpoint moved). Same shape as
    the drop payload, named distinctly so the contract/schema separate the two gestures (F2-D)."""

    id: str
    start: str
    end: str
    revision: NotRequired[int]
    client_mutation_id: NotRequired[str]


class RangeSelectPayload(TypedDict):
    """The payload for ``on_range_select`` — a new-event range created by dragging empty space.

    No ``id``/``revision`` (nothing exists yet); ``allDay`` distinguishes an all-day/date-granular
    selection from a timed one (F2-D). ``resourceId`` names the resource row the selection was
    drawn on (RF-28) so the host can create the event against the right resource — without it, a
    "create" gesture on a timeline row could not say WHICH row it meant.
    """

    start: str
    end: str
    allDay: bool
    resourceId: NotRequired[str]


class EventClickPayload(TypedDict):
    """The payload for ``on_event_click`` — the clicked event's id (F2-D)."""

    id: str


class ContextMenuPayload(TypedDict):
    """The payload for ``on_context_menu`` — ``id`` when the gesture landed on an event, ``start``
    when it landed on an empty slot (F2-D). At least one is present.

    This flat TypedDict is intentionally permissive (both keys ``NotRequired``) so the Reflex
    ``ObjectVar`` binding stays simple. The "at least one" refinement is NOT expressed by this flat
    type; it is enforced by the generated ``calendar-props.schema.json`` (``minProperties: 1``) and
    the TypeScript at-least-one union. The emitters only ever send a single key, and the
    schema/vitest contract lock rejects ``{}`` (see tests/test_calendar_schema.py).
    """

    id: NotRequired[str]
    start: NotRequired[str]


# ``from`` is a Python keyword, so the navigation payload uses the functional TypedDict syntax.
# It is the forward-compatible contract for ``on_view_change`` / ``on_range_change``; the F2-D
# interaction layer emits the mutation/selection events, while the navigation chrome that fires
# these is F2-E/F (declared here so the cross-language schema is complete, not wired yet).
ViewChangePayload = TypedDict(
    "ViewChangePayload",
    {"view": str, "from": str, "to": str},
)


_DroppedEventVar = ObjectVar[EventDropPayload]
_ResizeEventVar = ObjectVar[EventResizePayload]
_RangeSelectVar = ObjectVar[RangeSelectPayload]
_EventClickVar = ObjectVar[EventClickPayload]
_ContextMenuVar = ObjectVar[ContextMenuPayload]


def _on_event_drop_signature(event: _DroppedEventVar) -> list[rx.Var[EventDropPayload]]:
    """Pass the JS core's drop payload straight through, unmodified, to the backend handler."""
    return [event]


def _on_event_resize_signature(event: _ResizeEventVar) -> list[rx.Var[EventResizePayload]]:
    """Pass the JS core's resize payload straight through to the backend handler."""
    return [event]


def _on_range_select_signature(payload: _RangeSelectVar) -> list[rx.Var[RangeSelectPayload]]:
    """Pass the JS core's range-select payload straight through to the backend handler."""
    return [payload]


def _on_event_click_signature(payload: _EventClickVar) -> list[rx.Var[EventClickPayload]]:
    """Pass the clicked event's id straight through to the backend handler."""
    return [payload]


def _on_context_menu_signature(payload: _ContextMenuVar) -> list[rx.Var[ContextMenuPayload]]:
    """Pass the context-menu payload straight through to the backend handler."""
    return [payload]


_ViewChangeVar = ObjectVar[ViewChangePayload]


def _on_view_change_signature(payload: _ViewChangeVar) -> list[rx.Var[ViewChangePayload]]:
    """Pass the view-change payload (the new view + its visible range) through to the handler."""
    return [payload]


def _on_range_change_signature(payload: _ViewChangeVar) -> list[rx.Var[ViewChangePayload]]:
    """Pass the range-change payload (the new visible period) through to the handler."""
    return [payload]


class Calendar(rx.NoSSRComponent):
    """The AetherCal calendar: a production month view with drag-to-reschedule.

    Wraps the React bundle built to ``assets/aethercal-calendar.js`` (packaged into this wheel — see
    the module docstring). ``NoSSRComponent`` because drag-and-drop needs real browser DOM APIs
    (``dataTransfer`` etc.) that don't exist during any hypothetical server-side render pass.
    """

    library = _BUNDLE.importable_path
    tag = "AetherCalendar"
    is_default = False

    view: rx.Var[str] = rx_field(
        default=rx.Var.create("month"),
        doc='Which surface to render: "month" (F2-A) | "week" | "day" | "list" (F2-B/C).',
    )

    anchor: rx.Var[str] = rx_field(
        default=rx.Var.create(""),
        doc=(
            "Any day within the period to show, as a naive-local ISO date ('2026-07-15') or "
            "datetime ('2026-07-15T09:00:00'). Blank = today (resolved client-side). Bind this to "
            "state and update it from on_range_change / on_view_change for a controlled calendar."
        ),
    )

    events: rx.Var[list[CalendarEvent]] = rx_field(
        default=rx.Var.create([]),
        doc="Events to render, grouped onto the grid by each event's calendar day.",
    )

    resources: rx.Var[list[CalendarResource]] = rx_field(
        default=rx.Var.create([]),
        doc=(
            "Rows of the timeline view (RF-28), each {id, title, groupId?, color?}. Generic by "
            "design — AetherCal maps a resource to a host, but any array works. Events join a row "
            "by their resourceId; an event whose resourceId is missing or unknown is surfaced in "
            "an 'unassigned' row rather than dropped. Ignored by the other four views."
        ),
    )

    timeline_days: rx.Var[int] = rx_field(
        default=rx.Var.create(7),
        doc=(
            "How many days the timeline's horizontal axis spans, starting AT the anchor (1..31). "
            "Defaults to 7. Only the timeline view reads this."
        ),
    )

    locale: rx.Var[str] = rx_field(
        default=rx.Var.create("en"),
        doc="BCP-47 locale that drives weekday/date/time labels (i18n-ready; nothing hardcoded).",
    )

    first_day_of_week: rx.Var[int] = rx_field(
        default=rx.Var.create(1),
        doc="First day of the week, 0=Sunday … 6=Saturday. Defaults to Monday (1).",
    )

    theme: rx.Var[str | dict[str, str]] = rx_field(
        default=rx.Var.create("light"),
        doc=(
            "Theme: a preset name ('light' | 'dark' | 'midnight' | 'high_contrast') or a dict of "
            "--ac-* token overrides (e.g. Theme.dark().to_css_vars()). Applied as inline CSS "
            "variables; the default is the neutral 'light' preset."
        ),
    )

    navigation: rx.Var[bool] = rx_field(
        default=rx.Var.create(False),
        doc=(
            "Render the built-in navigation toolbar (previous / today / next + a period title + a "
            "view switcher). Off by default. When on, drive the calendar controlled: bind anchor / "
            "view to state and update them from on_range_change / on_view_change."
        ),
    )

    on_event_drop: rx.EventHandler[_on_event_drop_signature] = rx_field(
        doc=(
            "Fired when a user finishes dragging an event onto a new day/time. Receives the "
            "event's id plus its recomputed start/end (day and, on the time grid, hour), with the "
            "original duration preserved, plus revision and client_mutation_id."
        ),
    )

    on_event_resize: rx.EventHandler[_on_event_resize_signature] = rx_field(
        doc=(
            "Fired when a user drags an event's edge handle to change its duration (week/day time "
            "grid). Receives the event's id plus its recomputed start/end (one endpoint moved)."
        ),
    )

    on_range_select: rx.EventHandler[_on_range_select_signature] = rx_field(
        doc=(
            "Fired when a user drags across empty grid space to create a new event. Receives the "
            "selected start/end and whether it is an all-day range."
        ),
    )

    on_event_click: rx.EventHandler[_on_event_click_signature] = rx_field(
        doc="Fired when a user clicks an event. Receives the event's id.",
    )

    on_context_menu: rx.EventHandler[_on_context_menu_signature] = rx_field(
        doc=(
            "Fired on a right-click / context-menu gesture. Receives the event's id (on an event) "
            "or a start slot (on empty space)."
        ),
    )

    on_view_change: rx.EventHandler[_on_view_change_signature] = rx_field(
        doc=(
            "Fired when the view switcher picks a new view. Receives {view, from, to} for that "
            "view's range (same shape as on_range_change)."
        ),
    )

    on_range_change: rx.EventHandler[_on_range_change_signature] = rx_field(
        doc=(
            "Fired when previous / today / next change the period. Receives {view, from, to}; "
            "`from` is the new anchor and [from, to) is the period's events window."
        ),
    )

    @classmethod
    def create(cls, *children: rx.Component, **props: object) -> Calendar:
        """Create a `Calendar`, validating `view` against the supported contract values.

        Args:
            *children: Unused — the calendar renders its own grid, it takes no children.
            **props: Component props (`view`, `events`, `locale`, `first_day_of_week`,
                `on_event_drop`, ...).

        Returns:
            The created component.

        Raises:
            ValueError: If `view` is a literal string outside the calendar-view contract
                (`month`/`week`/`day`/`list`/`timeline`), if `first_day_of_week` is a literal int
                outside 0..6, or if `timeline_days` is a literal int outside 1..31. Dynamic
                `Var`-valued props (e.g. bound to backend state) are not checked here — that's a
                frontend contract enforced by the React layer's own defaults.
        """
        view = props.get("view")
        if isinstance(view, str) and view not in _VALID_VIEWS:
            valid = ", ".join(sorted(_VALID_VIEWS))
            msg = f"Calendar.view must be one of {{{valid}}}, got {view!r}"
            raise ValueError(msg)
        fdow = props.get("first_day_of_week")
        # `bool` is an `int` subclass in Python; reject True/False outright (a stray bool is not a
        # valid weekday index) and reject any literal int outside 0..6. A dynamic `Var` is skipped.
        if isinstance(fdow, bool) or (isinstance(fdow, int) and fdow not in range(7)):
            msg = f"Calendar.first_day_of_week must be 0..6 (0=Sunday), got {fdow!r}"
            raise ValueError(msg)
        days = props.get("timeline_days")
        # Same `bool`-is-an-`int` trap as above: a stray True/False is not a day count.
        if isinstance(days, bool) or (
            isinstance(days, int) and not _MIN_TIMELINE_DAYS <= days <= _MAX_TIMELINE_DAYS
        ):
            msg = (
                f"Calendar.timeline_days must be {_MIN_TIMELINE_DAYS}..{_MAX_TIMELINE_DAYS}, "
                f"got {days!r}"
            )
            raise ValueError(msg)
        theme = props.get("theme")
        # A literal theme string must be a known preset; a dict of --ac-* overrides or a dynamic Var
        # is passed through (the React layer resolves/sanitizes it).
        if isinstance(theme, str) and theme not in _VALID_THEMES:
            valid = ", ".join(sorted(_VALID_THEMES))
            msg = f"Calendar.theme string must be one of {{{valid}}}, got {theme!r}"
            raise ValueError(msg)
        anchor = props.get("anchor")
        # A literal anchor must be a well-formed, real ISO date/datetime; blank ("today") is ok,
        # and a dynamic Var (bound to state) is resolved defensively by the React layer.
        if isinstance(anchor, str) and anchor != "" and not _is_valid_anchor(anchor):
            msg = (
                "Calendar.anchor must be a naive-local ISO date ('2026-07-15') or datetime "
                f"('2026-07-15T09:00:00'), got {anchor!r}"
            )
            raise ValueError(msg)
        return super().create(*children, **props)  # type: ignore[return-value]
