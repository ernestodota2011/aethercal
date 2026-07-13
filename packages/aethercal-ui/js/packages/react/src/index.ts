/**
 * `@aethercal/calendar-react` — the React rendering layer for AetherCal.
 *
 * Ships NO React of its own (peer dependency) and no vendored styles beyond the neutral `--ac-*`
 * base theme. This is the esbuild entry the Reflex wrapper bundles; it must export the
 * `AetherCalendar` named component (the Reflex `tag`). Views beyond `month` are F2-B/C.
 */
export { AetherCalendar, default, type AetherCalendarProps } from "./AetherCalendar";
// Headless navigation helpers, re-exported for a consumer that builds its own controlled chrome:
// `getVisibleRange`/`stepAnchor` compute the visible period, `parseLocalDateTime` turns an emitted
// `from` string back into the anchor Date (F2-NAV).
export {
  getVisibleRange,
  parseLocalDateTime,
  stepAnchor,
} from "@aethercal/calendar-core";
export { CalendarNav, type CalendarNavProps } from "./CalendarNav";
export { OptimisticCalendar, type OptimisticCalendarProps } from "./OptimisticCalendar";
export {
  type CalendarMutation,
  type MutationResult,
  type UseOptimisticEventsOptions,
  type UseOptimisticEventsResult,
  useOptimisticEvents,
} from "./useOptimisticEvents";
export { CALENDAR_CSS, ensureCalendarStyles } from "./styles";
export { TimeGridView, type TimeGridViewProps } from "./TimeGridView";
export { TIME_GRID_CSS, ensureTimeGridStyles } from "./timeGridStyles";
export { TimelineView, type TimelineViewProps } from "./TimelineView";
export { TIMELINE_CSS, ensureTimelineStyles } from "./timelineStyles";
export {
  PRESETS,
  PRESET_NAMES,
  type ThemeInput,
  type ThemePreset,
  type ThemeTokens,
  defaultBaseTokenCss,
  defaultTimeGridTokenCss,
  defaultTimelineTokenCss,
  isThemePreset,
  resolveThemeVars,
} from "./theme";
export {
  type CalendarMessages,
  DEFAULT_LOCALE_MESSAGES,
  resolveMessages,
} from "./i18n";
export type {
  CalendarEvent,
  CalendarResource,
  CalendarView,
  ContextMenuPayload,
  Edge,
  EventClickPayload,
  EventDropPayload,
  EventResizePayload,
  FirstDayOfWeek,
  GridPoint,
  MutationKind,
  RangeSelectPayload,
  ViewChangePayload,
} from "@aethercal/calendar-core";
