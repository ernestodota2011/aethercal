/**
 * Headless chronological grouping for the list/agenda view (AetherCal-06 §5).
 *
 * Turns a flat list of events into an ordered, day-grouped agenda: days ascending, and within each
 * day the events sorted by start time (so a continuation of a multi-day event — which started on an
 * earlier day — sorts before that day's fresh events, and an all-day event at midnight sorts before
 * timed ones). A multi-day / all-day event is placed on every LOCAL calendar day it occupies, with
 * `isContinuation` / `continuesAfter` flags so the renderer can label the edges honestly without
 * re-deriving the span math (that lives here, once — the RF-23 boundary: no date logic in React).
 *
 * Like the rest of calendar-core this is DISPLAY-ONLY wall-time arithmetic (no timezone library);
 * authoritative scheduling lives in aethercal-core (Python, RF-23). Dependency-free and DOM-free.
 */
import { parseLocalDateTime } from "./dateMath";
import type { CalendarEvent } from "./types";

/** One event as it appears under a single agenda day. */
export interface AgendaEntry {
  event: CalendarEvent;
  /** This day is a later day of a multi-day event (it started on an earlier local day). */
  isContinuation: boolean;
  /** The event extends past this day (it ends on a later local day). */
  continuesAfter: boolean;
}

/** A single agenda day: its local date key and the events occurring on it, chronologically sorted. */
export interface AgendaDay {
  /** Local calendar day, "YYYY-MM-DD". */
  date: string;
  entries: AgendaEntry[];
}

/**
 * Defensive cap on the maximum number of local days enumerated for one event. Server-resolved
 * instances are bounded by the query window (RF-23), so a single instance longer than ~a year is
 * almost certainly malformed; capping enumeration keeps one bad event from making the grouping do
 * unbounded work. A capped event still renders from its start day, and its last shown day reads
 * `continuesAfter: true` (its real tail lies beyond what is shown).
 */
const MAX_EVENT_SPAN_DAYS = 370;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Local "YYYY-MM-DD" key for a Date (its date components, never UTC). */
function dayKey(dt: Date): string {
  return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
}

/** Midnight of `dt`'s local calendar day. */
function startOfDay(dt: Date): Date {
  return new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
}

/** `dt` advanced by `n` whole calendar days (component-based, so DST-safe). */
function addCalendarDays(dt: Date, n: number): Date {
  return new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() + n);
}

/** The ordered local-day keys an event occupies, plus its first/last key for edge flags. */
function occupiedDays(event: CalendarEvent): { keys: string[]; startKey: string; lastKey: string } {
  const startDt = parseLocalDateTime(event.start);
  const endDt = parseLocalDateTime(event.end);
  const startDay = startOfDay(startDt);

  let lastDay: Date;
  if (endDt.getTime() <= startDt.getTime()) {
    // Zero-duration or reversed range: the event occupies only its start day.
    lastDay = startDay;
  } else {
    // `end` is EXCLUSIVE, so an end exactly at local midnight does not occupy the end day. Stepping
    // back one millisecond before reading the day handles that boundary; DST-safe because only the
    // local date components are used.
    lastDay = startOfDay(new Date(endDt.getTime() - 1));
    if (lastDay.getTime() < startDay.getTime()) lastDay = startDay;
  }

  const keys: string[] = [];
  let cursor = startDay;
  for (let i = 0; i < MAX_EVENT_SPAN_DAYS && cursor.getTime() <= lastDay.getTime(); i += 1) {
    keys.push(dayKey(cursor));
    cursor = addCalendarDays(cursor, 1);
  }
  // With the cap engaged the true tail is beyond what we enumerate, so the last shown day keeps
  // `continuesAfter: true` (lastKey stays the un-shown real tail).
  return { keys, startKey: dayKey(startDay), lastKey: dayKey(lastDay) };
}

interface SortableEntry {
  entry: AgendaEntry;
  startMs: number;
  endMs: number;
  index: number;
}

/**
 * Group events into an ordered agenda. Days are ascending; within a day, entries are ordered by
 * start time then end time, ties broken by input order (stable). Each event lands on every local
 * day it occupies. Events sharing a day are merged into that day's single group.
 *
 * Strict about input: an invalid ISO `start`/`end` throws (via `parseLocalDateTime`) rather than
 * being silently dropped — the contract guarantees valid datetimes and hiding bad data hides bugs.
 */
export function buildAgenda(events: readonly CalendarEvent[]): AgendaDay[] {
  const byDay = new Map<string, SortableEntry[]>();

  events.forEach((event, index) => {
    const { keys, startKey, lastKey } = occupiedDays(event);
    const startMs = parseLocalDateTime(event.start).getTime();
    const endMs = parseLocalDateTime(event.end).getTime();
    for (const key of keys) {
      const sortable: SortableEntry = {
        entry: {
          event,
          isContinuation: key !== startKey,
          continuesAfter: key !== lastKey,
        },
        startMs,
        endMs,
        index,
      };
      const bucket = byDay.get(key);
      if (bucket) bucket.push(sortable);
      else byDay.set(key, [sortable]);
    }
  });

  // "YYYY-MM-DD" is fixed-width and zero-padded, so lexicographic order == chronological order.
  return [...byDay.keys()].sort().map((date) => {
    const bucket = byDay.get(date)!;
    bucket.sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs || a.index - b.index);
    return { date, entries: bucket.map((s) => s.entry) };
  });
}
