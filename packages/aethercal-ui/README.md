# aethercal-ui

The [AetherCal](https://github.com/ernestodota2011/aethercal) calendar component for
[Reflex](https://reflex.dev) — a Python wrapper around a custom React core. Five views,
drag-and-drop, theming and i18n.

```bash
pip install "aethercal-ui[reflex]"
```

The built JS bundle ships **inside the wheel**, so there is no Node toolchain to install and no
build step. (The `[reflex]` extra pulls in Reflex itself; the bare package stays lean.)

```python
import reflex as rx
from aethercal.ui import Calendar, CalendarEvent

class State(rx.State):
    view: str = "week"
    anchor: str = "2026-07-13"
    events: list[CalendarEvent] = [
        {"id": "1", "title": "Intro call",
         "start": "2026-07-13T09:00:00", "end": "2026-07-13T09:30:00"},
    ]

    @rx.event
    def on_drop(self, payload: dict):
        ...   # persist payload["id"], payload["start"], payload["end"]

    @rx.event
    def on_range_change(self, payload: dict):
        self.anchor = payload["from"]

def index() -> rx.Component:
    return Calendar.create(
        view=State.view,
        anchor=State.anchor,
        events=State.events,
        locale="es",
        theme="dark",
        navigation=True,
        on_event_drop=State.on_drop,
        on_range_change=State.on_range_change,
    )
```

## What it gives you

- **Five views** — `month`, `week`, `day`, `list`, and a **resource `timeline`** (resources as rows,
  time across the horizontal axis).
- **Drag and resize**, with optimistic reconciliation and rollback.
- **Four theme presets** (`light`, `dark`, `midnight`, `high_contrast`) or your own `--ac-*` token
  overrides. No CSS framework, no vendored styles.
- **i18n** — `en` and `es` message packs, with per-string overrides.
- **Keyboard parity** — every pointer gesture (grab, move, resize, create) has a keyboard path, and
  each grid exposes exactly one tab stop.

## Two things that will bite you

- **Annotate state with `CalendarEvent` / `CalendarResource`, not `dict`.** Reflex type-checks each
  prop against its declared type, so `events: list[dict]` is rejected outright with *"Invalid var
  passed for prop Calendar.events"*. Both TypedDicts are exported from `aethercal.ui`.
- **Events use naive local wall-time** (`"2026-07-13T09:00:00"` — no offset, no `Z`). AetherCal's
  API speaks UTC; convert at that boundary.

> On **Windows**, importing `aethercal.ui` makes Reflex symlink the component's shared asset, which
> Windows refuses without symlink privilege (`WinError 1314`). Enable **Developer Mode**. Linux and
> macOS are unaffected.

## Using React instead?

The same component ships to npm as
[`@aethercal/calendar-react`](https://www.npmjs.com/package/@aethercal/calendar-react).

Full prop reference:
**[docs/calendar-component.md](https://github.com/ernestodota2011/aethercal/blob/main/docs/calendar-component.md)**.
A runnable example lives in
[`examples/calendar/`](https://github.com/ernestodota2011/aethercal/tree/main/examples/calendar).

MIT licensed.
