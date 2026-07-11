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
  addCalendarDays,
  computeDroppedRange,
  formatLocalDateTime,
  getMonthGridDays,
  getWeekGridDays,
  parseLocalDateTime,
  startOfWeek,
  toDateOnly,
} from "./dateMath";
export { type GridNavKey, nextGridIndex } from "./keyboard";
export { getVisibleRange, stepAnchor } from "./navigation";
export {
  type DragAction,
  type DragState,
  dragReducer,
  initialDragState,
  isDragging,
} from "./dragMachine";
export {
  type InteractionAction,
  type InteractionState,
  activeEventId,
  initialInteractionState,
  interactionReducer,
  isDragging as isInteractionDragging,
  isIdle,
  isResizing,
  isSelecting,
} from "./interactionMachine";
export {
  DEFAULT_SNAP_MINUTES,
  clampMinuteToWindow,
  computeMovedRange,
  computeRangeSelection,
  computeResize,
  fractionToMinuteOfDay,
  stepInstantMinutes,
} from "./interactions";
export {
  type AppliedEvents,
  type OptimisticOverride,
  type OverrideStatus,
  type ReconcileAction,
  type ReconcileState,
  applyOverrides,
  initialReconcileState,
  reconcileReducer,
  selectSettledIds,
} from "./reconcile";
export {
  buildTimeGrid,
  type HourMark,
  layoutDayColumn,
  nowMarkerFraction,
  resolveTimeGridConfig,
  splitAllDay,
  type ResolvedTimeGridConfig,
  type TimeGrid,
  type TimeGridBlock,
  type TimeGridColumn,
  type TimeGridConfig,
} from "./timeGrid";
export type {
  CalendarEvent,
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
} from "./types";
