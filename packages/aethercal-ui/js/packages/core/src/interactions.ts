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
const MS_PER_MINUTE = 60_000;
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
  const snapped = Math.round(raw / step) * step;
  return clamp(snapped, windowStartMin, windowEndMin);
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
  const target = parseLocalDateTime(`${targetDateOnly}T00:00:00`);
  // The move = shift the whole event by (dayDelta calendar days, minuteDelta wall minutes), applied
  // identically to both endpoints so the local duration is preserved.
  const dayDelta = calendarDayDelta(originalStart, target);
  const startMinuteOfDay = originalStart.getHours() * MINUTES_PER_HOUR + originalStart.getMinutes();
  const minuteDelta = minuteOfDay - startMinuteOfDay;

  const shift = (dt: Date): Date =>
    new Date(
      dt.getFullYear(),
      dt.getMonth(),
      dt.getDate() + dayDelta,
      dt.getHours(),
      dt.getMinutes() + minuteDelta,
      dt.getSeconds(),
    );

  const payload: EventDropPayload = {
    id: event.id,
    start: formatLocalDateTime(shift(originalStart)),
    end: formatLocalDateTime(shift(originalEnd)),
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
  const minDurationMs = (opts.minDurationMinutes ?? DEFAULT_SNAP_MINUTES) * MS_PER_MINUTE;
  const start = parseLocalDateTime(event.start);
  const end = parseLocalDateTime(event.end);
  const candidate = dateAtMinute(targetDateOnly, minuteOfDay);

  let newStart = start;
  let newEnd = end;
  if (edge === "end") {
    const minEndMs = start.getTime() + minDurationMs;
    newEnd = new Date(Math.max(candidate.getTime(), minEndMs));
  } else {
    const maxStartMs = end.getTime() - minDurationMs;
    newStart = new Date(Math.min(candidate.getTime(), maxStartMs));
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
    endDate = new Date(startDate.getTime() + minDurationMinutes * MS_PER_MINUTE);
  }
  return { start: formatLocalDateTime(startDate), end: formatLocalDateTime(endDate), allDay: false };
}
