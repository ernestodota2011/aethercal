/**
 * `@aethercal/calendar-react` — the React rendering layer for AetherCal.
 *
 * Ships NO React of its own (peer dependency) and no vendored styles beyond the neutral `--ac-*`
 * base theme. This is the esbuild entry the Reflex wrapper bundles; it must export the
 * `AetherCalendar` named component (the Reflex `tag`). Views beyond `month` are F2-B/C.
 */
export { AetherCalendar, default, type AetherCalendarProps } from "./AetherCalendar";
export { CALENDAR_CSS, ensureCalendarStyles } from "./styles";
export { TimeGridView, type TimeGridViewProps } from "./TimeGridView";
export { TIME_GRID_CSS, ensureTimeGridStyles } from "./timeGridStyles";
export type {
  CalendarEvent,
  CalendarView,
  EventDropPayload,
  FirstDayOfWeek,
} from "@aethercal/calendar-core";
