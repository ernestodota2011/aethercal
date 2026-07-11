/**
 * Pure time-grid geometry for the week/day views (AetherCal-06 §5, F2-B).
 *
 * Headless and framework-agnostic (no React, no DOM, no CSS) — the same RF-23 boundary the month
 * geometry lives under: this only arranges what is already on screen in the browser's local
 * wall-time. Authoritative, timezone-correct scheduling stays in `aethercal-core` (Python).
 *
 * Positions are emitted as fractions in [0, 1] of the visible day window (top/height) and as a
 * lane index + lane count for horizontal overlap packing, so the React layer stays purely
 * presentational (percentages → CSS). The day view is a week with a single column, so both views
 * share this one engine.
 */
import { occupiedDayBounds, parseLocalDateTime } from "./dateMath";
import type { CalendarEvent } from "./types";

const MINUTES_PER_HOUR = 60;
const MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR;
const MS_PER_DAY = 86_400_000;

/** Caller-facing time-grid window: which hours of the day are visible. */
export interface TimeGridConfig {
  /** First hour shown (0..23). Default 0 (midnight). */
  dayStartHour?: number;
  /** Last hour shown, exclusive (1..24). Default 24. */
  dayEndHour?: number;
}

/** A validated window with the derived minute span; every geometry function takes this. */
export interface ResolvedTimeGridConfig {
  dayStartHour: number;
  dayEndHour: number;
  windowMinutes: number;
}

/** One timed event positioned in a day column. */
export interface TimeGridBlock {
  event: CalendarEvent;
  /** 0-based column (lane) within this event's overlap cluster. */
  lane: number;
  /** Number of lanes in the cluster; the block's width is `1 / laneCount`. */
  laneCount: number;
  /** Vertical start as a fraction [0, 1] of the visible window (clamped). */
  topFraction: number;
  /** Height as a fraction [0, 1] of the visible window (clamped so `top + height <= 1`). */
  heightFraction: number;
  /** This column is a later day of a multi-day event (it started on an earlier local day). */
  isContinuation: boolean;
  /** The event extends past this column (it ends on a later local day). */
  continuesAfter: boolean;
}

/** A single day of the grid: its all-day events and its positioned timed blocks. */
export interface TimeGridColumn {
  dateOnly: string;
  allDay: CalendarEvent[];
  timed: TimeGridBlock[];
}

/** An hour gridline/label position. */
export interface HourMark {
  hour: number;
  topFraction: number;
}

/** The fully-resolved geometry for a week/day view. */
export interface TimeGrid {
  columns: TimeGridColumn[];
  hourMarks: HourMark[];
  config: ResolvedTimeGridConfig;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Normalize a caller window into a valid, non-empty visible range. Bad or inverted inputs
 * (a plain-JS consumer can pass anything) degrade to the full 0..24 day rather than dividing by a
 * zero-width window downstream.
 */
export function resolveTimeGridConfig(config: TimeGridConfig = {}): ResolvedTimeGridConfig {
  const rawStart = config.dayStartHour;
  const rawEnd = config.dayEndHour;
  const start =
    Number.isFinite(rawStart) && rawStart !== undefined ? clamp(Math.trunc(rawStart), 0, 23) : 0;
  const end =
    Number.isFinite(rawEnd) && rawEnd !== undefined ? clamp(Math.trunc(rawEnd), 1, 24) : 24;
  // An inverted or empty window is meaningless — fall back to the full day.
  const [dayStartHour, dayEndHour] = end > start ? [start, end] : [0, 24];
  return {
    dayStartHour,
    dayEndHour,
    windowMinutes: (dayEndHour - dayStartHour) * MINUTES_PER_HOUR,
  };
}

/** Partition events into all-day (never on the time grid) and timed. */
export function splitAllDay(events: readonly CalendarEvent[]): {
  allDay: CalendarEvent[];
  timed: CalendarEvent[];
} {
  const allDay: CalendarEvent[] = [];
  const timed: CalendarEvent[] = [];
  for (const event of events) {
    if (event.allDay === true) allDay.push(event);
    else timed.push(event);
  }
  return { allDay, timed };
}

/**
 * Minutes from the column's midnight to `iso`, in WALL-CLOCK terms (may exceed 1440 for a
 * cross-midnight end). DST-safe by construction — the same component-based approach as
 * `computeDroppedRange`: it counts whole calendar days between the two local midnights and adds the
 * event's local minute-of-day, instead of subtracting timestamps (which would be off by ±60 min on
 * a 23/25-hour DST-transition day and mis-position the block vertically).
 */
function minutesFromMidnight(iso: string, columnMidnight: Date): number {
  const dt = parseLocalDateTime(iso);
  const dtMidnight = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const dayDelta = Math.round((dtMidnight.getTime() - columnMidnight.getTime()) / MS_PER_DAY);
  const minuteOfDay = dt.getHours() * MINUTES_PER_HOUR + dt.getMinutes() + dt.getSeconds() / 60;
  return dayDelta * MINUTES_PER_DAY + minuteOfDay;
}

/** Half-open overlap on the raw event times ([start, end)); touching endpoints do NOT overlap. */
function eventsOverlap(a: CalendarEvent, b: CalendarEvent): boolean {
  const aStart = parseLocalDateTime(a.start).getTime();
  const aEnd = parseLocalDateTime(a.end).getTime();
  const bStart = parseLocalDateTime(b.start).getTime();
  const bEnd = parseLocalDateTime(b.end).getTime();
  return aStart < bEnd && bStart < aEnd;
}

/**
 * Lay out a single day's timed events: pack overlapping events into lanes and compute each one's
 * vertical position within the window.
 *
 * Lane packing is the classic sweep used by Google Calendar / FullCalendar: events are grouped
 * into maximal overlap clusters (a gap where nothing is still running closes a cluster), each event
 * takes the first lane whose previous occupant has already ended, and every event in a cluster
 * shares that cluster's lane count (its rendered width is `1 / laneCount`). No lane ever holds two
 * overlapping events; a fully sequential day collapses to a single lane.
 */
export function layoutDayColumn(
  events: readonly CalendarEvent[],
  dateOnly: string,
  config: ResolvedTimeGridConfig,
): TimeGridBlock[] {
  const columnMidnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
  const windowStartMin = config.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = config.dayEndHour * MINUTES_PER_HOUR;

  // Sort by start ascending, then by end descending so the longest event of a tie seeds the cluster.
  const ordered = [...events].sort((a, b) => {
    const sa = parseLocalDateTime(a.start).getTime();
    const sb = parseLocalDateTime(b.start).getTime();
    if (sa !== sb) return sa - sb;
    return parseLocalDateTime(b.end).getTime() - parseLocalDateTime(a.end).getTime();
  });

  const blocks: TimeGridBlock[] = [];
  // `lanes[i]` holds the last event placed in lane i for the current cluster.
  let lanes: CalendarEvent[] = [];
  // Indices into `blocks` for the events of the current cluster (to backfill laneCount).
  let clusterBlockIdx: number[] = [];
  let clusterEndMs = Number.NEGATIVE_INFINITY;

  const closeCluster = () => {
    const laneCount = lanes.length;
    for (const idx of clusterBlockIdx) blocks[idx]!.laneCount = laneCount;
    lanes = [];
    clusterBlockIdx = [];
    clusterEndMs = Number.NEGATIVE_INFINITY;
  };

  for (const event of ordered) {
    const startMin = minutesFromMidnight(event.start, columnMidnight);
    const endMin = minutesFromMidnight(event.end, columnMidnight);
    // Skip events with no visible extent in this window (entirely before or after the visible
    // hours). Otherwise they clamp to a zero-height sliver pinned at the grid edge AND steal an
    // overlap lane from the events that ARE visible. Only relevant for a narrowed window.
    if (endMin <= windowStartMin || startMin >= windowEndMin) continue;

    const startMs = parseLocalDateTime(event.start).getTime();
    const endMs = parseLocalDateTime(event.end).getTime();

    // A new event starting at/after everything running in the cluster begins a fresh cluster.
    if (clusterBlockIdx.length > 0 && startMs >= clusterEndMs) closeCluster();

    let lane = lanes.findIndex((occupant) => !eventsOverlap(occupant, event));
    if (lane === -1) {
      lane = lanes.length;
      lanes.push(event);
    } else {
      lanes[lane] = event;
    }

    const top = clamp(startMin, windowStartMin, windowEndMin);
    const bottom = clamp(endMin, top, windowEndMin);
    const topFraction = (top - windowStartMin) / config.windowMinutes;
    const heightFraction = (bottom - top) / config.windowMinutes;

    // Where this column sits in the event's local-day span (exclusive end), so the renderer can show
    // an honest label per day — the event's start time on its start day, a continuation label on a
    // later day — instead of the start time bleeding onto every day it crosses. Same `occupiedDayBounds`
    // criterion the agenda view uses, so week/day and list agree on the edges.
    const { startKey, lastKey } = occupiedDayBounds(event);

    clusterBlockIdx.push(blocks.length);
    blocks.push({
      event,
      lane,
      laneCount: 1,
      topFraction,
      heightFraction,
      isContinuation: dateOnly !== startKey,
      continuesAfter: dateOnly !== lastKey,
    });
    clusterEndMs = Math.max(clusterEndMs, endMs);
  }
  closeCluster();

  return blocks;
}

/** The hour gridlines/labels for a resolved window (one per hour, from start to end-1). */
function hourMarksFor(config: ResolvedTimeGridConfig): HourMark[] {
  const marks: HourMark[] = [];
  for (let hour = config.dayStartHour; hour < config.dayEndHour; hour += 1) {
    marks.push({
      hour,
      topFraction: ((hour - config.dayStartHour) * MINUTES_PER_HOUR) / config.windowMinutes,
    });
  }
  return marks;
}

/**
 * Build the full week/day geometry: one column per requested day (in order), each with its all-day
 * events and its lane-packed timed blocks. Events whose start day is not among `dateOnlys` are not
 * in view and are dropped. The day view passes a single date; the week view passes seven.
 */
export function buildTimeGrid(
  dateOnlys: readonly string[],
  events: readonly CalendarEvent[],
  config: TimeGridConfig | ResolvedTimeGridConfig = {},
): TimeGrid {
  const resolved: ResolvedTimeGridConfig =
    "windowMinutes" in config ? config : resolveTimeGridConfig(config);
  const { allDay, timed } = splitAllDay(events);

  // Precompute each timed event's absolute [start, end) so a cross-midnight/multi-day event is
  // rendered in EVERY visible day it overlaps (each column clamps it via layoutDayColumn), not only
  // its start day — otherwise an overnight event silently vanishes after midnight.
  const timedSpans = timed.map((event) => ({
    event,
    startTs: parseLocalDateTime(event.start).getTime(),
    endTs: parseLocalDateTime(event.end).getTime(),
  }));

  const columns: TimeGridColumn[] = dateOnlys.map((dateOnly) => {
    const midnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
    const dayStartTs = midnight.getTime();
    // DST-safe next midnight (component-based, never a raw +24h).
    const nextTs = new Date(
      midnight.getFullYear(),
      midnight.getMonth(),
      midnight.getDate() + 1,
    ).getTime();
    const dayTimed = timedSpans
      .filter((s) => {
        if (s.startTs >= nextTs) return false;
        if (s.endTs > dayStartTs) return true;
        // Zero-duration event ([start, end) empty) still belongs to the day it starts on.
        return s.startTs === s.endTs && s.startTs >= dayStartTs;
      })
      .map((s) => s.event);
    // An all-day event covers the local days it occupies, treating its `end` as EXCLUSIVE (the same
    // criterion as occupiedDays/buildAgenda): an end exactly at local midnight does NOT add the end
    // day, and a single-day event (start === end) still covers its one day. Using the shared
    // `occupiedDayBounds` keeps the week/day grid and the agenda in agreement on the span.
    const dayAllDay = allDay.filter((event) => {
      const { startKey, lastKey } = occupiedDayBounds(event);
      return startKey <= dateOnly && dateOnly <= lastKey;
    });
    return {
      dateOnly,
      allDay: dayAllDay,
      timed: layoutDayColumn(dayTimed, dateOnly, resolved),
    };
  });

  return { columns, hourMarks: hourMarksFor(resolved), config: resolved };
}

/**
 * The fraction [0, 1] of the window at which a "now" line should be drawn for `now`'s wall-clock
 * time-of-day, or `null` when `now` falls outside the visible window. The caller decides which day
 * column (if any) the line belongs to by matching `now`'s date.
 */
export function nowMarkerFraction(
  now: Date,
  config: TimeGridConfig | ResolvedTimeGridConfig = {},
): number | null {
  const resolved: ResolvedTimeGridConfig =
    "windowMinutes" in config ? config : resolveTimeGridConfig(config);
  const minutes = now.getHours() * MINUTES_PER_HOUR + now.getMinutes() + now.getSeconds() / 60;
  const windowStartMin = resolved.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = resolved.dayEndHour * MINUTES_PER_HOUR;
  // The window is half-open [start, end): the end hour is exclusive (dayEndHour), so a `now`
  // exactly at the end boundary belongs to the next, invisible slot and draws no line.
  if (minutes < windowStartMin || minutes >= windowEndMin) return null;
  return (minutes - windowStartMin) / resolved.windowMinutes;
}
