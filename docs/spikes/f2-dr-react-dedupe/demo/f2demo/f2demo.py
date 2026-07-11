"""Minimal Reflex app that mounts the AetherCal Calendar component.

F2-DR React-dedupe spike. The point is to force Reflex's real frontend build
(Vite + react-router) to process our packaged TSX bundle (which keeps react /
react/jsx-runtime EXTERNAL) alongside Reflex's own React, and prove that only a
single React instance ends up in the tree (no "Invalid hook call").

To make the test meaningful we also drive a real state <-> component round trip:
- `Calendar` internally uses React hooks (useMemo/useCallback) and jsx-runtime.
- We wire `on_event_drop` to a backend event handler and render a state Var next
  to the calendar. If the component's React were a second instance, its hooks
  would throw at render time; a clean build + a mounted, interactive calendar is
  the empirical signal that host and component share one React.

See docs/spikes/f2-dr-react-dedupe.md for the full evidence and verdict.
"""

import reflex as rx

from aethercal.ui.calendar import Calendar


class DedupeState(rx.State):
    """Trivial state to prove the host<->component React context bridges cleanly."""

    last_drop: str = "(no drop yet)"

    @rx.event
    def on_drop(self, payload: dict) -> None:  # payload is the JS drop object
        self.last_drop = str(payload)


def index() -> rx.Component:
    return rx.vstack(
        rx.heading("F2-DR - React dedupe spike"),
        rx.text("Last drop payload: "),
        rx.text(DedupeState.last_drop),
        Calendar.create(
            view="month",
            events=[
                {
                    "id": "evt-1",
                    "title": "Demo event",
                    "start": "2026-07-15T10:00:00",
                    "end": "2026-07-15T11:00:00",
                }
            ],
            on_event_drop=DedupeState.on_drop,
        ),
        spacing="4",
        padding="2rem",
    )


app = rx.App()
app.add_page(index, route="/")
