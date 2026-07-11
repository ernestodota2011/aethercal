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

from typing import NotRequired, TypedDict

import reflex as rx
from reflex.vars import ObjectVar
from reflex_base.components.component import field as rx_field

_BUNDLE = rx.asset(path="assets/aethercal-calendar.js", shared=True)

# The four calendar surfaces (AetherCal-06 §5). Only ``month`` renders in F2-A; the rest are the
# forward-looking contract (F2-B/C). Kept in sync with the TS ``CalendarView`` union
# (js/packages/core/src/types.ts).
_VALID_VIEWS = frozenset({"month", "week", "day", "list"})


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


class EventDropPayload(TypedDict):
    """The payload the JS core sends back on ``on_event_drop`` (js/packages/core/src/types.ts)."""

    id: str
    start: str
    end: str
    revision: NotRequired[int]


_DroppedEventVar = ObjectVar[EventDropPayload]


def _on_event_drop_signature(event: _DroppedEventVar) -> list[rx.Var[EventDropPayload]]:
    """Pass the JS core's drop payload straight through, unmodified, to the backend handler."""
    return [event]


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

    events: rx.Var[list[CalendarEvent]] = rx_field(
        default=rx.Var.create([]),
        doc="Events to render, grouped onto the grid by each event's calendar day.",
    )

    locale: rx.Var[str] = rx_field(
        default=rx.Var.create("en"),
        doc="BCP-47 locale that drives weekday/date/time labels (i18n-ready; nothing hardcoded).",
    )

    first_day_of_week: rx.Var[int] = rx_field(
        default=rx.Var.create(1),
        doc="First day of the week, 0=Sunday … 6=Saturday. Defaults to Monday (1).",
    )

    on_event_drop: rx.EventHandler[_on_event_drop_signature] = rx_field(
        doc=(
            "Fired when a user finishes dragging an event onto a new day. Receives the "
            "event's id plus its recomputed start/end (same day count, original duration "
            "and time-of-day preserved)."
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
                (`month`/`week`/`day`/`list`), or if `first_day_of_week` is a literal int outside
                0..6. Dynamic `Var`-valued props (e.g. bound to backend state) are not checked
                here — that's a frontend contract enforced by the React layer's own defaults.
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
        return super().create(*children, **props)  # type: ignore[return-value]
