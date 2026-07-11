/**
 * `@aethercal/calendar-core` — the headless, framework-agnostic calendar core.
 *
 * Pure TypeScript: grid geometry + interaction state machines + the cross-language contract
 * types. NO React, NO styles (enforced by the ESLint boundary + AetherCal-06 §3 / RF-23). The
 * React rendering layer is `@aethercal/calendar-react`.
 */
export {
  type AgendaDay,
  type AgendaEntry,
  buildAgenda,
} from "./agenda";
export {
  computeDroppedRange,
  formatLocalDateTime,
  getMonthGridDays,
  getWeekGridDays,
  parseLocalDateTime,
  startOfWeek,
  toDateOnly,
} from "./dateMath";
export {
  type DragAction,
  type DragState,
  dragReducer,
  initialDragState,
  isDragging,
} from "./dragMachine";
export type {
  CalendarEvent,
  CalendarView,
  EventDropPayload,
  FirstDayOfWeek,
} from "./types";
