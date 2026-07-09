"""Reflex wrapper around the AetherCal calendar core (a custom TSX component).

This is the F0-10 de-risking spike (Spike A) — see ``docs/spikes/f0-10-reflex-tsx.md`` for the
GO/ADJUST verdict. It proves the pipeline end to end: a hand-written TSX calendar core is built
to a single JS bundle, that bundle ships *inside this package's wheel* (see
``[tool.hatch.build.targets.wheel].artifacts`` in ``packages/aethercal-ui/pyproject.toml``; the
bundle is a committed, reproducible build artifact -- ``pnpm build`` regenerates it), and this
module wraps it as a real
``rx.Component`` with typed props flowing in and a real event trigger flowing out. It is
deliberately minimal — the full-featured F2 calendar is a separate, larger build.

Reflex's public ``reflex``/``rx`` namespace re-exports ``Component``, ``NoSSRComponent``, ``Var``,
``EventHandler`` and the ``event_spec`` helpers, but *not* the ``field()`` used to declare a
component prop with a docstring — that only exists on the internal ``reflex_base`` package (the
same one every bundled Reflex add-on component, e.g. ``reflex_components_react_player``, imports
from). This module follows that same convention; see the decision doc for why that's a real
version-fragility risk worth flagging rather than hiding.

The ``icalendar`` library gets the same treatment in
``aethercal.core.ical.serde`` for a *partially*-typed dependency; here the whole ``reflex``/
``reflex_base`` boundary is untyped from pyright's point of view (no ``py.typed`` marker was
found for either at strict-check time), so every reflex-facing symbol is imported explicitly and
re-typed at the boundary — nothing "Any"-shaped leaks into this module's own public surface.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

import reflex as rx
from reflex.vars import ObjectVar
from reflex_base.components.component import field as rx_field

_BUNDLE = rx.asset(path="assets/aethercal-calendar.js", shared=True)


class CalendarEvent(TypedDict):
    """One calendar event, matching the JS core's ``CalendarEvent`` type (js/src/types.ts)."""

    id: str
    title: str
    start: str  # ISO 8601, naive local wall-time, e.g. "2026-07-09T14:00:00".
    end: str
    color: NotRequired[str]


class EventDropPayload(TypedDict):
    """The payload the JS core sends back on ``on_event_drop`` (js/src/types.ts)."""

    id: str
    start: str
    end: str


_DroppedEventVar = ObjectVar[EventDropPayload]


def _on_event_drop_signature(event: _DroppedEventVar) -> list[rx.Var[EventDropPayload]]:
    """Pass the JS core's drop payload straight through, unmodified, to the backend handler."""
    return [event]


class Calendar(rx.NoSSRComponent):
    """The AetherCal calendar: month/week grids with drag-to-reschedule.

    Wraps the TSX core built to ``assets/aethercal-calendar.js`` (packaged into this wheel — see
    the module docstring). ``NoSSRComponent`` because drag-and-drop needs real browser DOM APIs
    (``dataTransfer`` etc.) that don't exist during any hypothetical server-side render pass.
    """

    library = _BUNDLE.importable_path
    tag = "AetherCalendar"
    is_default = False

    view: rx.Var[str] = rx_field(
        default=rx.Var.create("month"),
        doc='Which grid to render: "month" or "week".',
    )

    events: rx.Var[list[CalendarEvent]] = rx_field(
        default=rx.Var.create([]),
        doc="Events to render, grouped onto the grid by each event's calendar day.",
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
        """Create a `Calendar`, validating `view` against its two supported values.

        Args:
            *children: Unused — the calendar renders its own grid, it takes no children.
            **props: Component props (`view`, `events`, `on_event_drop`, ...).

        Returns:
            The created component.

        Raises:
            ValueError: If `view` is a literal string outside `{"month", "week"}`. A
                dynamic `Var`-valued `view` (e.g. bound to backend state) is not checked here —
                that's a frontend contract enforced by the TSX core's own default fallback.
        """
        view = props.get("view")
        if isinstance(view, str) and view not in {"month", "week"}:
            msg = f'Calendar.view must be "month" or "week", got {view!r}'
            raise ValueError(msg)
        return super().create(*children, **props)  # type: ignore[return-value]
