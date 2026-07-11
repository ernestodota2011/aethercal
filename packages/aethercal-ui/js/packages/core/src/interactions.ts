/**
 * Pure interaction geometry for the calendar (AetherCal-06 §6, F2-D).
 *
 * Turns a gesture (drag-move with a time change, resize an edge, select a range) plus a snapped
 * minute-of-day into new event times / a new-event range. Headless and DOM-free like the rest of
 * `calendar-core` (RF-23): the React layer maps a pointer position to a fraction/minute and calls
 * these; all wall-time rearrangement is display-only, authoritative scheduling stays server-side.
 *
 * All results are naive local "YYYY-MM-DDTHH:mm:ss" strings via `formatLocalDateTime`, and event
 * `revision` is echoed through unchanged (the server assigns the *new* revision on accept — §4).
 */
import {
  addCalendarDays,
  calendarDayDelta,
  computeDroppedRange,
  formatLocalDateTime,
  parseLocalDateTime,
} from "./dateMath";
import type { ResolvedTimeGridConfig } from "./timeGrid";
import type {
  CalendarEvent,
  Edge,
  EventDropPayload,
  EventResizePayload,
  GridPoint,
  RangeSelectPayload,
} from "./types";

const MINUTES_PER_HOUR = 60;
/** Default snap and minimum-slot granularity (minutes). */
export const DEFAULT_SNAP_MINUTES = 15;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** A `Date` at `minuteOfDay` minutes past the local midnight of `dateOnly` (minutes normalize into hours). */
function dateAtMinute(dateOnly: string, minuteOfDay: number): Date {
  const midnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
  return new Date(midnight.getFullYear(), midnight.getMonth(), midnight.getDate(), 0, minuteOfDay, 0);
}

/**
 * A `Date` `minutes` after `dt` in LOCAL wall-clock terms (component-based, so it is DST-safe — a
 * raw millisecond add would shift the wall clock by the DST hour across a transition). Used for the
 * minimum-duration clamps so a resized/selected slot keeps its intended local length.
 */
function addLocalMinutes(dt: Date, minutes: number): Date {
  return new Date(dt.getFullYear(), dt.getMonth(), dt.getDate(), dt.getHours(), dt.getMinutes() + minutes, dt.getSeconds());
}

/**
 * Map a fraction [0, 1] of the visible day window to an absolute minute-of-day, snapped to
 * `snapMinutes` and clamped to the window. The inverse of the `topFraction` the time grid renders,
 * so a pointer at a given vertical position resolves back to a wall-clock minute.
 */
export function fractionToMinuteOfDay(
  fraction: number,
  config: ResolvedTimeGridConfig,
  snapMinutes: number = DEFAULT_SNAP_MINUTES,
): number {
  const windowStartMin = config.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = config.dayEndHour * MINUTES_PER_HOUR;
  const raw = windowStartMin + clamp(fraction, 0, 1) * config.windowMinutes;
  const step = snapMinutes > 0 ? snapMinutes : DEFAULT_SNAP_MINUTES;
  // Snap the grid to the WINDOW START, not midnight: with a step that does not divide the window
  // start (e.g. a 45-minute step on an 08:00 window), anchoring at midnight would shift the top of
  // the window off the grid so fraction 0 no longer lands on the window start.
  const snapped = windowStartMin + Math.round((raw - windowStartMin) / step) * step;
  return clamp(snapped, windowStartMin, windowEndMin);
}

/**
 * Clamp a minute-of-day into the time grid's visible window `[dayStartHour*60, dayEndHour*60]`.
 *
 * The keyboard counterpart of the pointer's `fractionToMinuteOfDay` clamp: a keyboard move/resize
 * steps the target minute by a snap increment and calls this so it can never leave the visible
 * hours (F2-E a11y). Pure and DOM-free like the rest of `calendar-core` (RF-23).
 */
export function clampMinuteToWindow(minuteOfDay: number, config: ResolvedTimeGridConfig): number {
  return clamp(minuteOfDay, config.dayStartHour * MINUTES_PER_HOUR, config.dayEndHour * MINUTES_PER_HOUR);
}

const MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR;

/**
 * Step a `(dateOnly, minuteOfDay)` INSTANT by `deltaMinutes`, treating day and minute as one point
 * in time: a step that crosses local midnight rolls the day (so an event ending exactly at 00:00 can
 * be shortened to 23:45 of the previous day), then the resulting minute is clamped into the visible
 * window (F2-E keyboard move/resize, Crisol round-8). `minuteOfDay === 1440` is the day's midnight
 * end and is kept (not rolled). Pure and DOM-free (RF-23).
 */
export function stepInstantMinutes(
  dateOnly: string,
  minuteOfDay: number,
  deltaMinutes: number,
  config: ResolvedTimeGridConfig,
): { dateOnly: string; minuteOfDay: number } {
  let m = minuteOfDay + deltaMinutes;
  let d = dateOnly;
  while (m < 0) {
    m += MINUTES_PER_DAY;
    d = addCalendarDays(d, -1);
  }
  while (m > MINUTES_PER_DAY) {
    m -= MINUTES_PER_DAY;
    d = addCalendarDays(d, 1);
  }
  return { dateOnly: d, minuteOfDay: clampMinuteToWindow(m, config) };
}

/**
 * Recompute an event's range when it is moved. With `minuteOfDay === null` this is a day-only move
 * that preserves the wall-clock time-of-day (delegates to `computeDroppedRange`); with a minute it
 * is a time-grid move that changes BOTH the day and the time-of-day.
 *
 * DST-safe by construction (consistent with `computeDroppedRange`): it applies the SAME calendar-day
 * and minute-of-day shift to BOTH endpoints with component-based date arithmetic, rather than adding
 * a raw millisecond duration. This preserves the event's LOCAL (wall-clock) duration — the visible
 * block length stays the same — instead of its absolute duration, which a raw ms add would keep at
 * the cost of shifting the wall time by the DST hour. Display-only; authoritative scheduling and any
 * zone-aware duration semantics live server-side (RF-23).
 */
export function computeMovedRange(
  event: CalendarEvent,
  targetDateOnly: string,
  minuteOfDay: number | null,
): EventDropPayload {
  if (minuteOfDay === null) return computeDroppedRange(event, targetDateOnly);

  const originalStart = parseLocalDateTime(event.start);
  const originalEnd = parseLocalDateTime(event.end);
  // The new start is placed exactly on the snapped grid slot (seconds zeroed); the new end preserves
  // the event's LOCAL span — whole calendar days + minute-of-day delta — applied with component
  // arithmetic so it is DST-safe. Sub-minute seconds are intentionally dropped by the snap.
  const newStart = dateAtMinute(targetDateOnly, minuteOfDay);
  const daySpan = calendarDayDelta(originalStart, originalEnd);
  const startMinuteOfDay = originalStart.getHours() * MINUTES_PER_HOUR + originalStart.getMinutes();
  const endMinuteOfDay = originalEnd.getHours() * MINUTES_PER_HOUR + originalEnd.getMinutes();
  const minuteSpan = endMinuteOfDay - startMinuteOfDay;
  const newEnd = new Date(
    newStart.getFullYear(),
    newStart.getMonth(),
    newStart.getDate() + daySpan,
    newStart.getHours(),
    newStart.getMinutes() + minuteSpan,
    0,
  );

  const payload: EventDropPayload = {
    id: event.id,
    start: formatLocalDateTime(newStart),
    end: formatLocalDateTime(newEnd),
  };
  if (event.revision !== undefined) payload.revision = event.revision;
  return payload;
}

/**
 * Recompute an event's range when one edge is dragged to `minuteOfDay` on `targetDateOnly`. The
 * opposite endpoint is held fixed and a minimum duration is enforced (dragging the end above the
 * start, or the start past the end, clamps to a `minDurationMinutes` slot instead of inverting).
 */
export function computeResize(
  event: CalendarEvent,
  edge: Edge,
  targetDateOnly: string,
  minuteOfDay: number,
  opts: { minDurationMinutes?: number } = {},
): EventResizePayload {
  const minDurationMinutes = opts.minDurationMinutes ?? DEFAULT_SNAP_MINUTES;
  const start = parseLocalDateTime(event.start);
  const end = parseLocalDateTime(event.end);
  const candidate = dateAtMinute(targetDateOnly, minuteOfDay);

  let newStart = start;
  let newEnd = end;
  if (edge === "end") {
    // Keep at least a minDuration slot, measured in LOCAL minutes (DST-safe).
    const minEnd = addLocalMinutes(start, minDurationMinutes);
    newEnd = candidate.getTime() >= minEnd.getTime() ? candidate : minEnd;
  } else {
    const maxStart = addLocalMinutes(end, -minDurationMinutes);
    newStart = candidate.getTime() <= maxStart.getTime() ? candidate : maxStart;
  }

  const payload: EventResizePayload = {
    id: event.id,
    start: formatLocalDateTime(newStart),
    end: formatLocalDateTime(newEnd),
  };
  if (event.revision !== undefined) payload.revision = event.revision;
  return payload;
}

/**
 * Compute a new-event range from a select gesture's two grid points (anchor + current), independent
 * of drag direction. A date-granular selection (`minuteOfDay === null`: month cell / all-day rail)
 * yields an all-day range spanning the covered days with an exclusive next-midnight end; a timed
 * selection yields the [earlier, later] instants, with a zero-length click widened to a minimum slot.
 */
export function computeRangeSelection(
  anchor: GridPoint,
  current: GridPoint,
  opts: { minDurationMinutes?: number } = {},
): RangeSelectPayload {
  const minDurationMinutes = opts.minDurationMinutes ?? DEFAULT_SNAP_MINUTES;
  const allDay = anchor.minuteOfDay === null || current.minuteOfDay === null;

  if (allDay) {
    const [earlier, later] =
      anchor.dateOnly <= current.dateOnly
        ? [anchor.dateOnly, current.dateOnly]
        : [current.dateOnly, anchor.dateOnly];
    const startDate = parseLocalDateTime(`${earlier}T00:00:00`);
    const laterMidnight = parseLocalDateTime(`${later}T00:00:00`);
    // Exclusive next-midnight end so a single all-day cell spans exactly its own day.
    const endDate = new Date(
      laterMidnight.getFullYear(),
      laterMidnight.getMonth(),
      laterMidnight.getDate() + 1,
    );
    return { start: formatLocalDateTime(startDate), end: formatLocalDateTime(endDate), allDay: true };
  }

  const a = dateAtMinute(anchor.dateOnly, anchor.minuteOfDay ?? 0);
  const c = dateAtMinute(current.dateOnly, current.minuteOfDay ?? 0);
  const startDate = a.getTime() <= c.getTime() ? a : c;
  let endDate = a.getTime() <= c.getTime() ? c : a;
  if (endDate.getTime() === startDate.getTime()) {
    endDate = addLocalMinutes(startDate, minDurationMinutes);
  }
  return { start: formatLocalDateTime(startDate), end: formatLocalDateTime(endDate), allDay: false };
}
