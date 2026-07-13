/**
 * Pure date-grid geometry for the calendar core: month/week grid generation and the
 * start/end recomputation used when an event is dropped on a new day.
 *
 * Deliberately dependency-free (no date library) and DISPLAY-ONLY — timezone-correct scheduling
 * math lives in `aethercal-core` (Python, RF-23). This only rearranges what is already on screen,
 * in the browser's local wall-time. Ported from the F0-10 spike's `dateMath.ts` and generalized
 * so the week can start on any day (F2-A: default Monday; other views/locales reuse it).
 */
import type { CalendarEvent, EventDropPayload } from "./types";

/** Default first day of the week (Monday), using `Date.getDay()` numbering (0 = Sunday). */
const DEFAULT_FIRST_DAY_OF_WEEK = 1;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * Parse "YYYY-MM-DD[THH:mm[:ss]]" as local time (never as UTC, unlike bare `new Date(iso)`).
 *
 * Strict: the pattern is anchored at both ends (no trailing garbage), time components are
 * range-checked, and the constructed date is verified against its parts so calendar overflow
 * (e.g. "2026-02-30", "2026-13-01") is rejected rather than silently normalized.
 */
export function parseLocalDateTime(iso: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(iso.trim());
  if (!match) {
    throw new Error(`invalid ISO datetime: ${iso}`);
  }
  const [, ys, mos, ds, hs, mis, ss] = match;
  const y = Number(ys);
  const mo = Number(mos);
  const d = Number(ds);
  const h = Number(hs ?? "0");
  const mi = Number(mis ?? "0");
  const s = Number(ss ?? "0");
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) {
    throw new Error(`out-of-range ISO datetime: ${iso}`);
  }
  const dt = new Date(y, mo - 1, d, h, mi, s);
  // Reject a date that does not exist on the calendar (Date would otherwise roll it forward).
  // Only the date parts are checked: a wall time that a DST spring-forward skips is normalized
  // by the platform and is left as-is (display-only; scheduling correctness is server-side, RF-23).
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
    throw new Error(`nonexistent calendar date: ${iso}`);
  }
  return dt;
}

/** Format a `Date` back to a naive local "YYYY-MM-DDTHH:mm:ss" string (inverse of parse). */
export function formatLocalDateTime(dt: Date): string {
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}T${pad2(dt.getHours())}:${pad2(dt.getMinutes())}:${pad2(dt.getSeconds())}`;
}

/** The date-only portion ("YYYY-MM-DD") of an ISO datetime string. */
export function toDateOnly(iso: string): string {
  const dt = parseLocalDateTime(iso);
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
}

/** Local "YYYY-MM-DD" key for a `Date` (its local date components, never UTC). */
function dayKey(dt: Date): string {
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
}

/**
 * The first and last LOCAL calendar day an event occupies, as "YYYY-MM-DD" keys, treating the
 * event's `end` as EXCLUSIVE (the iCalendar DTEND convention): an end exactly at local midnight
 * does NOT occupy the end day. A zero-duration or reversed range occupies only its start day.
 * DST-safe — only local date components are read, never a raw millisecond span.
 *
 * The single source of truth for "which local days does this event cover", shared by the agenda
 * grouping (buildAgenda) and the week/day grid (buildTimeGrid) so both surfaces draw the SAME span
 * (and agree on the start / pass-through / final-day edges of a multi-day event).
 */
export function occupiedDayBounds(event: Pick<CalendarEvent, "start" | "end">): {
  startKey: string;
  lastKey: string;
} {
  const startDt = parseLocalDateTime(event.start);
  const endDt = parseLocalDateTime(event.end);
  const startDay = new Date(startDt.getFullYear(), startDt.getMonth(), startDt.getDate());
  let lastDay = startDay;
  if (endDt.getTime() > startDt.getTime()) {
    // Step back 1ms so an end exactly at local midnight lands on the previous day (exclusive end);
    // then read that day's local date. Clamp to the start day for a range that stays within a day.
    const beforeEnd = new Date(endDt.getTime() - 1);
    const candidate = new Date(beforeEnd.getFullYear(), beforeEnd.getMonth(), beforeEnd.getDate());
    if (candidate.getTime() > startDay.getTime()) lastDay = candidate;
  }
  return { startKey: dayKey(startDay), lastKey: dayKey(lastDay) };
}

/** Offset (0..6) of `dt` from the configured first day of the week. */
function dayOffsetFromWeekStart(dt: Date, firstDayOfWeek: number): number {
  return (dt.getDay() - firstDayOfWeek + 7) % 7;
}

/** Midnight on the first-day-of-week of the week containing `dt` (default Monday). */
export function startOfWeek(dt: Date, firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK): Date {
  const start = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  start.setDate(start.getDate() - dayOffsetFromWeekStart(start, firstDayOfWeek));
  return start;
}

function dateRange(start: Date, count: number): string[] {
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  });
}

/** The 7 date-only strings for the week containing `anchor` (first day configurable). */
export function getWeekGridDays(
  anchor: Date,
  firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK,
): string[] {
  return dateRange(startOfWeek(anchor, firstDayOfWeek), 7);
}

/** The 42 date-only strings (6 full weeks) covering the month containing `anchor`. */
export function getMonthGridDays(
  anchor: Date,
  firstDayOfWeek: number = DEFAULT_FIRST_DAY_OF_WEEK,
): string[] {
  const firstOfMonth = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  return dateRange(startOfWeek(firstOfMonth, firstDayOfWeek), 42);
}

/**
 * The `count` date-only strings of the resource timeline's window (RF-28), starting AT `anchor`'s
 * day. Unlike the week/month grids this is NOT week-aligned: the timeline's span is configurable, and
 * only a 7-day window could be week-aligned coherently — so it simply starts where it is anchored.
 */
export function getTimelineGridDays(anchor: Date, count: number): string[] {
  return dateRange(new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate()), count);
}

/**
 * Add (or subtract) whole calendar days to a "YYYY-MM-DD" key, returning a "YYYY-MM-DD" key.
 *
 * Component-based (never a raw millisecond add) so it is DST-safe and rolls correctly across month
 * and year boundaries. Used by keyboard drag to step a move target by ±1 day / ±1 week without a
 * pointer (F2-E a11y). Any time-of-day in the input is ignored — this operates on the date only.
 */
export function addCalendarDays(dateOnly: string, delta: number): string {
  const base = parseLocalDateTime(`${toDateOnly(dateOnly)}T00:00:00`);
  const shifted = new Date(base.getFullYear(), base.getMonth(), base.getDate() + delta);
  return `${shifted.getFullYear()}-${pad2(shifted.getMonth() + 1)}-${pad2(shifted.getDate())}`;
}

/** Whole calendar-day difference between two dates, DST-safe (a day is 23–25h across DST). */
export function calendarDayDelta(from: Date, to: Date): number {
  const fromMidnight = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const toMidnight = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((toMidnight.getTime() - fromMidnight.getTime()) / 86_400_000);
}

/**
 * Recompute an event's start/end after it is dropped on `targetDateOnly`, preserving each
 * endpoint's wall-clock time-of-day and the whole-day span between them.
 *
 * DST-safe by construction: it shifts BOTH endpoints by the same number of *calendar days*
 * (component-based date arithmetic) rather than adding a raw millisecond duration — so it never
 * gains or loses the DST hour. This is display-only wall-time rearrangement; authoritative
 * scheduling (and any zone-aware duration semantics) live server-side in aethercal-core (RF-23).
 * Echoes the event's `revision` through when present (the server assigns the *new* revision on
 * accept — F2-D).
 */
export function computeDroppedRange(
  event: CalendarEvent,
  targetDateOnly: string,
): EventDropPayload {
  const originalStart = parseLocalDateTime(event.start);
  const originalEnd = parseLocalDateTime(event.end);
  const target = parseLocalDateTime(targetDateOnly);
  const dayDelta = calendarDayDelta(originalStart, target);

  const newStart = new Date(
    originalStart.getFullYear(),
    originalStart.getMonth(),
    originalStart.getDate() + dayDelta,
    originalStart.getHours(),
    originalStart.getMinutes(),
    originalStart.getSeconds(),
  );
  const newEnd = new Date(
    originalEnd.getFullYear(),
    originalEnd.getMonth(),
    originalEnd.getDate() + dayDelta,
    originalEnd.getHours(),
    originalEnd.getMinutes(),
    originalEnd.getSeconds(),
  );

  const payload: EventDropPayload = {
    id: event.id,
    start: formatLocalDateTime(newStart),
    end: formatLocalDateTime(newEnd),
  };
  if (event.revision !== undefined) {
    payload.revision = event.revision;
  }
  return payload;
}
