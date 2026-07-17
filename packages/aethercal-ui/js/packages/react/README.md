# @aethercal/calendar-react

The React calendar from [AetherCal](https://github.com/ernestodota2011/aethercal): **month, week,
day, list and resource-timeline** views, drag-and-drop with optimistic reconciliation, theming and
i18n.

No CSS framework, no vendored styles, and **no bundled React** — it is a peer dependency, so there
is never a second copy.

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

  return (
    <AetherCalendar
      view="week"
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

`start` and `end` are **naive local wall-time** ISO strings — no offset, no `Z`. The calendar renders
the clock time you hand it.

## The five views

| `view` | Renders |
|---|---|
| `month` | Month grid, overflow collapsing into "+N more" |
| `week` | Seven-day time grid with an all-day rail and a "now" line |
| `day` | Single-day time grid |
| `list` | Agenda list, grouped by day |
| `timeline` | **Resources as rows**, time across the horizontal axis |

The timeline's resources are generic — a row is whatever you say it is (people, rooms, machines). An
event joins a row by `resourceId`; one whose `resourceId` is missing or unknown lands in an
"unassigned" row rather than vanishing.

## Also in the box

- **Optimistic updates** — `useOptimisticEvents` / `OptimisticCalendar` apply a drag immediately, and
  roll it back if the server rejects it or never answers.
- **Theming** — four presets (`light`, `dark`, `midnight`, `high_contrast`) or your own `--ac-*`
  token overrides.
- **i18n** — `en` and `es` message packs, per-string overrides, `Intl`-driven date and time labels.
- **Keyboard parity** — grab, move, resize and create all work from the keyboard, and each grid
  exposes exactly one tab stop.
- **A headless core** — [`@aethercal/calendar-core`](https://www.npmjs.com/package/@aethercal/calendar-core)
  holds the geometry and the state machines with no React at all, if you render differently.

Using Python? The same component ships for [Reflex](https://reflex.dev) as
[`aethercal-ui`](https://pypi.org/project/aethercal-ui/).

Full prop reference:
**[docs/calendar-component.md](https://github.com/ernestodota2011/aethercal/blob/main/docs/calendar-component.md)**.

MIT licensed.
