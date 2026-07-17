# @aethercal/calendar-core

The headless calendar core behind [AetherCal](https://github.com/ernestodota2011/aethercal): grid
geometry and interaction state machines, in pure TypeScript.

**No React. No styles. No framework.** Just the maths and the state transitions a calendar needs, so
you can render one with anything. An ESLint boundary keeps React out of this package for good.

```bash
npm install @aethercal/calendar-core
```

Most people want the rendering layer instead:
[`@aethercal/calendar-react`](https://www.npmjs.com/package/@aethercal/calendar-react).

## What is in it

| Area | Exports |
|---|---|
| Grids | `getMonthGridDays`, `getWeekGridDays`, `getTimelineGridDays`, `buildTimeGrid`, `buildResourceTimeline` |
| Layout | `packLanes`, `packLanesBy`, `layoutDayColumn`, `layoutTimelineEvent`, `splitAllDay` |
| Agenda | `buildAgenda` |
| Interactions | `dragReducer`, `interactionReducer`, `computeMovedRange`, `computeResize`, `computeRangeSelection` |
| Optimistic state | `reconcileReducer`, `applyOverrides`, `selectSettledIds` |
| Navigation | `getVisibleRange`, `stepAnchor` |
| Date maths | `addCalendarDays`, `startOfWeek`, `parseLocalDateTime`, `formatLocalDateTime`, `toDateOnly` |
| Keyboard | `nextGridIndex` |
| Contract types | `CalendarEvent`, `CalendarResource`, `CalendarView`, and the gesture payload types |

```ts
import { buildTimeGrid, packLanes } from "@aethercal/calendar-core";
```

The lane-packing sweep is **axis-agnostic**: the same routine packs overlapping events into columns
on a week grid and into rows on a resource timeline.

Times are naive **local wall-time** ISO strings (`"2026-07-13T09:00:00"`) — no offset, no `Z`.

MIT licensed.
