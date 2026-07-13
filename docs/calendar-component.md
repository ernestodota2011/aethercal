# The calendar component

An embeddable calendar with five views, drag-and-drop, theming and i18n. It ships in two forms from
one codebase:

| Package | For |
|---|---|
| **`@aethercal/calendar-react`** (npm) | A React application |
| **`aethercal-ui`** (PyPI) | A [Reflex](https://reflex.dev) application — a Python wrapper over the same bundle |

There is no styling framework and no vendored CSS-in-JS: the component paints itself with `--ac-*`
CSS custom properties, so it inherits your design system instead of fighting it. React is a **peer**
dependency — the component never bundles its own copy.

Beneath both sits `@aethercal/calendar-core`: headless, framework-free grid geometry and interaction
state machines (no React, no styles). Use it directly if you render with something other than React.

---

## React

```bash
npm install @aethercal/calendar-react react react-dom
```

```tsx
import { AetherCalendar } from "@aethercal/calendar-react";
import { useState } from "react";

const events = [
  { id: "1", title: "Intro call", start: "2026-07-13T09:00:00", end: "2026-07-13T09:30:00" },
];

export function Agenda() {
  const [anchor, setAnchor] = useState("2026-07-13");
  const [view, setView] = useState<"week" | "month">("week");

  return (
    <AetherCalendar
      view={view}
      anchor={anchor}
      events={events}
      locale="es"
      theme="dark"
      navigation
      onRangeChange={(p) => setAnchor(p.from)}
      onEventDrop={(p) => save(p.id, p.start, p.end)}
    />
  );
}
```

`start` and `end` are **naive local wall-time** ISO strings (`"2026-07-13T09:00:00"`) — no offset,
no `Z`. The calendar renders the wall-clock time you hand it. Convert at your boundary: the API
speaks UTC, the grid speaks local.

With `navigation`, the component is **controlled**: it renders the toolbar and *emits* the new
period, but you own `anchor` and `view` in state. Ignore the events and it will not move.

## The five views

| `view` | What it renders |
|---|---|
| `month` | Month grid; overflow collapses into "+N more" |
| `week` | Seven-day time grid with an all-day rail and a "now" line |
| `day` | Single-day time grid |
| `list` | Agenda list, grouped by day |
| `timeline` | **Resources as rows**, time on the horizontal axis |

### The resource timeline

```tsx
<AetherCalendar
  view="timeline"
  timelineDays={5}                      // horizontal span, 1..31 (default 7)
  resources={[
    { id: "h1", title: "Dr. Rivera", groupId: "Clinic A" },
    { id: "h2", title: "Dr. Nakamura", groupId: "Clinic A" },
    { id: "h3", title: "Dr. Oyelaran", groupId: "Clinic B" },
  ]}
  events={[{ id: "1", title: "Intro", start: "...", end: "...", resourceId: "h1" }]}
  defaultCollapsedGroupIds={["Clinic B"]}
  onToggleGroup={(groupId, collapsed) => persist(groupId, collapsed)}
/>
```

A resource is **generic** — the component does not know what one *is*. AetherCal's backend maps a
resource to a host, but the same timeline serves rooms, chairs or machines with no code change.
`groupId` is both the grouping key and the group header's label.

An event whose `resourceId` is missing or unknown appears in an **"unassigned" row**, never silently
dropped. Dragging an event across rows emits `onEventDrop` carrying the **target** `resourceId`.

> **There is no backend operation for a cross-row drag yet.** A booking's host lives on its event
> type, so moving one between hosts means changing its event type — a different duration and a
> different slug. The component supports the gesture; AetherCal's own admin ships it disabled until
> that semantics is designed. Wire it into your own app and you own that decision.

## Events

| Prop | Fires when | Payload |
|---|---|---|
| `onEventDrop` | An event is dropped on a new day/time | `{ id, start, end, revision?, client_mutation_id?, resourceId? }` |
| `onEventResize` | An edge handle changes a duration (week/day) | `{ id, start, end, revision?, client_mutation_id? }` |
| `onRangeSelect` | Empty space is dragged to create | `{ start, end, allDay, resourceId? }` |
| `onEventClick` | An event is clicked | `{ id }` |
| `onContextMenu` | Right-click on an event or an empty slot | `{ id? , start? }` — one of the two |
| `onRangeChange` | Prev / today / next moves the period | `{ view, from, to }` |
| `onViewChange` | The view switcher changes the view | `{ view, from, to }` |

Set `editable: false` on an event to make it undraggable — and enforce it on the server too: a
client-side flag is a hint, not a permission.

### Optimistic updates

`useOptimisticEvents` (and its `OptimisticCalendar` wrapper) applies a drag immediately, then
reconciles with the server: on acceptance the server's confirmed times and `revision` win; on
rejection **or timeout** the event rolls back to where it was, so the UI never sticks in a pending
state. A `clientMutationId` rides along so a retried mutation can be deduplicated server-side, and a
stale or out-of-order response (a `revision` that is not newer) is ignored.

```tsx
import { OptimisticCalendar } from "@aethercal/calendar-react";

<OptimisticCalendar
  events={events}
  view="week"
  mutate={async ({ payload, clientMutationId }) => {
    const saved = await api.move(payload.id, payload.start, payload.end, clientMutationId);
    // Resolve with the server's truth; throw/reject to roll the gesture back.
    return { id: saved.id, start: saved.start, end: saved.end, revision: saved.revision };
  }}
/>;
```

## Theming

Four presets — `light`, `dark`, `midnight`, `high_contrast`. Pass a preset name, or an object of
`--ac-*` token overrides for anything in between.

```tsx
<AetherCalendar theme="midnight" />
<AetherCalendar theme={{ "--ac-event-accent": "#c2410c", "--ac-focus": "#0ea5e9" }} />
```

Seventeen tokens cover the surface (`--ac-fg`, `--ac-bg`, `--ac-border`, `--ac-cell-bg`,
`--ac-event-bg`, `--ac-event-accent`, `--ac-focus`, `--ac-tg-now`, …). They are applied as inline CSS
variables on the calendar root, so they cascade through every view. The presets' foreground /
background pairs are contrast-asserted in the test suite.

From Python:

```python
from aethercal.ui.theme import Theme

Theme.dark().to_css_vars()                       # -> {"--ac-fg": "...", ...}
Theme.preset("high_contrast").to_css_vars()
```

## Internationalization

`locale` is a BCP-47 tag; it drives every weekday, date and time label through `Intl` — nothing is
hardcoded. Message packs ship for **`en`** and **`es`**, resolved by falling back from an exact tag
(`es-MX`) to its primary subtag (`es`) to English.

```tsx
<AetherCalendar locale="es-MX" />                                        // -> the `es` pack
<AetherCalendar locale="en" messages={{ noEvents: "Nothing today" }} />  // override one string
```

`messages` overrides individual strings on top of the resolved pack, so you can adjust wording — or
add a language — without forking. Several entries are functions (`more(n)`, `endsAt(label)`,
`movedTo(label)`) because a translation is a sentence, not a concatenation.

## Accessibility

Every pointer gesture has a keyboard equivalent: grab, move, resize and create-in-place all work
from the keyboard, and each grid exposes exactly **one tab stop**, so a calendar full of events does
not become a tab trap. Live-region messages announce the outcome ("moved to…", "resized to…") —
which is why those strings are part of the i18n pack, not hardcoded English.

---

## Reflex (Python)

```bash
pip install "aethercal-ui[reflex]"
```

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

> **Annotate the state with `CalendarEvent` / `CalendarResource`, not `dict`.** Reflex type-checks a
> prop against its declared type, so a field annotated `list[dict]` is rejected when the component is
> built: *"Invalid var passed for prop Calendar.events"*. Both TypedDicts are exported from
> `aethercal.ui` for exactly this.

The Python props are the React props in `snake_case` (`first_day_of_week`, `timeline_days`,
`on_event_drop`). Keys *inside* a payload stay `camelCase` (`resourceId`, `allDay`) — they are the
JS object's own keys, crossing the boundary unchanged.

A **literal** prop is validated when the component is constructed: an unknown `view`, a
`first_day_of_week` outside 0..6, a `timeline_days` outside 1..31, an unknown theme name or a
malformed `anchor` each raise `ValueError` at build time instead of rendering something wrong. A prop
bound to state is resolved by the React layer at runtime.

A runnable Reflex example lives in [`examples/calendar/`](../examples/calendar/).

> **On Windows:** importing `aethercal.ui` makes Reflex symlink the component's shared asset, which
> Windows refuses without symlink privilege (`WinError 1314`). Enable **Developer Mode**, or set
> `REFLEX_BACKEND_ONLY=1` if you only need to import the module — that is what this repository's own
> test suite does. Linux and macOS are unaffected.
