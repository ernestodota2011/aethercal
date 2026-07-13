/**
 * Pure resource-timeline geometry (RF-28): resources in ROWS, time on the HORIZONTAL axis.
 *
 * The fifth calendar surface, and the transpose of the week/day grid: where `timeGrid` gives each
 * DAY a column and stacks overlapping events sideways within it, this gives each RESOURCE a row and
 * stacks its overlapping events downwards within it. Both share the one lane-packing sweep
 * (`packLanes`) and the one DST-safe day↔minute mapping (`minutesFromMidnight`) — the sweep is
 * axis-agnostic, so the timeline reuses it rather than re-deriving it.
 *
 * Headless and framework-agnostic (no React, no DOM, no CSS — the RF-23 boundary): it only arranges
 * what is already on screen in the browser's local wall-time. Authoritative, timezone-correct
 * scheduling stays in `aethercal-core` (Python).
 *
 * The axis is a CONCATENATION of each visible day's visible hour window: with a narrowed window
 * (say 8..18) the invisible night hours do not exist on the axis at all, so a continuous event that
 * crosses midnight renders as ONE unbroken bar across the compressed boundary rather than a seam of
 * per-day fragments. That is what a compressed axis means, and `coalesceSegments` enforces it.
 *
 * Two deliberate design calls, both about not lying to the user:
 *
 * 1. ALL-DAY events ride the same axis as timed ones, normalized to the whole local days they
 *    occupy. Week/day need a separate all-day rail because a vertical TIME axis cannot express "no
 *    time"; a horizontal DAY-spanning axis expresses an all-day event natively, as a full-day bar.
 *    They lane-stack against timed events of the same resource, which is exactly right — an all-day
 *    booking really does collide with a 10:00 meeting.
 * 2. Events whose `resourceId` is absent, or names a resource that was not passed in, are NOT
 *    dropped: they surface in a synthetic UNASSIGNED row (`resource: null`) at the bottom. Silently
 *    swallowing an event because of a bad id is the kind of bug you only find in production.
 */
import {
  addCalendarDays,
  formatLocalDateTime,
  occupiedDayBounds,
  parseLocalDateTime,
  toDateOnly,
} from "./dateMath";
import { DEFAULT_SNAP_MINUTES } from "./interactions";
import {
  type LanePlacement,
  type ResolvedTimeGridConfig,
  type TimeGridConfig,
  minutesFromMidnight,
  packLanes,
  resolveTimeGridConfig,
} from "./timeGrid";
import type { CalendarEvent, CalendarResource, GridPoint } from "./types";

const MINUTES_PER_HOUR = 60;

/** Default span of the timeline's horizontal axis, in days. */
export const DEFAULT_TIMELINE_DAYS = 7;
/** A timeline must show at least one day. */
export const MIN_TIMELINE_DAYS = 1;
/** Beyond a month the axis is too dense to read; kept in lockstep with the Python wrapper's bound. */
export const MAX_TIMELINE_DAYS = 31;

/** The timeline's window: the visible hours (as in the time grid) plus which groups are collapsed. */
export interface ResourceTimelineConfig extends TimeGridConfig {
  /** Groups whose resource rows are hidden. The group's own header still renders. */
  collapsedGroupIds?: readonly string[];
}

/** Either form of the config the builder accepts (raw, or already resolved by a caller). */
type TimelineConfigInput =
  | ResourceTimelineConfig
  | (ResolvedTimeGridConfig & { collapsedGroupIds?: readonly string[] });

/** One event positioned in a resource row: a horizontal bar, lane-stacked against its overlaps. */
export interface TimelineBlock {
  event: CalendarEvent;
  /** 0-based lane (sub-row) within this event's overlap cluster — the VERTICAL stack inside the row. */
  lane: number;
  /** Lanes in the cluster; the bar's height within the row is `1 / laneCount`. */
  laneCount: number;
  /** Horizontal start as a fraction [0, 1] of the whole axis (clamped to the window). */
  leftFraction: number;
  /** Width as a fraction [0, 1] of the whole axis (clamped so `left + width <= 1`). */
  widthFraction: number;
  /** This event is all-day (normalized to whole days); the renderer may style it differently. */
  allDay: boolean;
  /** The event begins before the visible window — the bar is clipped at the left edge. */
  continuesBefore: boolean;
  /** The event runs past the visible window — the bar is clipped at the right edge. */
  continuesAfter: boolean;
}

/** One row of the timeline: a resource (or the synthetic unassigned row) and its positioned bars. */
export interface TimelineRow {
  /** The resource, or `null` for the synthetic "unassigned" row. */
  resource: CalendarResource | null;
  /** The group this row belongs to, or `null` when ungrouped. */
  groupId: string | null;
  blocks: TimelineBlock[];
  /** Lanes the row must be tall enough for — its widest cluster (always >= 1). */
  laneCount: number;
}

/** A collapsible group header. Its `id` doubles as its display label (see `CalendarResource`). */
export interface TimelineGroup {
  id: string;
  collapsed: boolean;
  /** Resources in the group — reported even while collapsed, so the header can say what it hides. */
  resourceCount: number;
}

/** The display list, in render order: group headers interleaved with the rows they contain. */
export type TimelineItem =
  | { kind: "group"; group: TimelineGroup }
  | { kind: "row"; row: TimelineRow };

/** One day's slice of the horizontal axis (every day gets an equal share). */
export interface TimelineDayHeader {
  dateOnly: string;
  leftFraction: number;
  widthFraction: number;
}

/** An hour gridline on the horizontal axis. */
export interface TimelineTick {
  dateOnly: string;
  hour: number;
  leftFraction: number;
  /** First tick of a day — the renderer draws a stronger rule at a day boundary. */
  isDayStart: boolean;
}

/** The fully-resolved geometry for a resource timeline. */
export interface ResourceTimeline {
  days: string[];
  items: TimelineItem[];
  dayHeaders: TimelineDayHeader[];
  ticks: TimelineTick[];
  config: ResolvedTimeGridConfig;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Normalize a caller's day count into the supported range. A plain-JS consumer can pass anything; a
 * 0 / negative / NaN / fractional count would otherwise build a degenerate axis (a zero-width window
 * divides by zero downstream).
 */
export function resolveTimelineDays(days?: number): number {
  if (days === undefined || !Number.isFinite(days)) return DEFAULT_TIMELINE_DAYS;
  return clamp(Math.trunc(days), MIN_TIMELINE_DAYS, MAX_TIMELINE_DAYS);
}

/** Resolve either config form into the hour window the geometry runs on. */
function resolveWindow(config: TimelineConfigInput): ResolvedTimeGridConfig {
  return "windowMinutes" in config ? config : resolveTimeGridConfig(config);
}

/**
 * The `[start, end)` the timeline lays an event out on. A timed event uses its own times; an ALL-DAY
 * event is normalized to the whole local days it occupies (via the shared `occupiedDayBounds`), so
 * it spans full day-widths on the axis — and so a zero-length all-day event (`start === end`) still
 * covers its day instead of collapsing into an invisible zero-width sliver.
 */
function timelineSpan(event: CalendarEvent): { start: string; end: string } {
  if (event.allDay !== true) return { start: event.start, end: event.end };
  const { startKey, lastKey } = occupiedDayBounds(event);
  return { start: `${startKey}T00:00:00`, end: `${addCalendarDays(lastKey, 1)}T00:00:00` };
}

/** An event plus the span it is laid out on — the unit `packLanes` sorts and packs. */
interface RowItem {
  event: CalendarEvent;
  start: string;
  end: string;
}

/** A visible run of an item on the axis, in global axis-minutes. */
interface Segment {
  startMin: number;
  endMin: number;
  /** The item really begins earlier than this segment does (clipped on the left). */
  clippedStart: boolean;
  /** The item really ends later than this segment does (clipped on the right). */
  clippedEnd: boolean;
}

/**
 * The visible segments of one span, one per visible day, in global axis-minutes. A day where the
 * span has no visible extent contributes nothing. The skip rule mirrors `layoutDayColumn`'s, so a
 * zero-duration event inside the window still yields a (zero-width) segment rather than vanishing.
 */
function segmentsFor(
  span: RowItem,
  days: readonly string[],
  config: ResolvedTimeGridConfig,
): Segment[] {
  const windowStartMin = config.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = config.dayEndHour * MINUTES_PER_HOUR;
  const segments: Segment[] = [];

  days.forEach((dateOnly, index) => {
    const midnight = parseLocalDateTime(`${dateOnly}T00:00:00`);
    const startMin = minutesFromMidnight(span.start, midnight);
    const endMin = minutesFromMidnight(span.end, midnight);
    if (endMin <= windowStartMin || startMin >= windowEndMin) return;

    const visibleStart = clamp(startMin, windowStartMin, windowEndMin);
    const visibleEnd = clamp(endMin, visibleStart, windowEndMin);
    const dayOrigin = index * config.windowMinutes;
    segments.push({
      startMin: dayOrigin + (visibleStart - windowStartMin),
      endMin: dayOrigin + (visibleEnd - windowStartMin),
      clippedStart: startMin < windowStartMin,
      clippedEnd: endMin > windowEndMin,
    });
  });

  return segments;
}

/**
 * Merge segments that are adjacent on the axis into one bar.
 *
 * A continuous event's visible parts are ALWAYS contiguous in axis-minutes (day i's segment ends at
 * exactly where day i+1's begins — both derived from the same integer window bounds), so a
 * continuous event always coalesces to exactly one bar, with no per-day seam. Non-contiguous `days`
 * (which the type permits even though every caller passes a contiguous range) degrade honestly into
 * separate bars.
 */
function coalesceSegments(segments: readonly Segment[]): Segment[] {
  const merged: Segment[] = [];
  for (const segment of segments) {
    const last = merged[merged.length - 1];
    if (last && last.endMin === segment.startMin) {
      last.endMin = segment.endMin;
      last.clippedEnd = segment.clippedEnd;
    } else {
      merged.push({ ...segment });
    }
  }
  return merged;
}

/** Lay out one resource row: pack its events into lanes, then place each one on the axis. */
function layoutRow(
  items: readonly RowItem[],
  days: readonly string[],
  config: ResolvedTimeGridConfig,
): TimelineBlock[] {
  const totalMinutes = days.length * config.windowMinutes;
  if (totalMinutes <= 0) return [];

  // Drop items with no visible extent BEFORE packing — an off-window event must never steal a lane
  // from the ones actually on screen (the same rule `layoutDayColumn` follows).
  const placements: LanePlacement<RowItem>[] = packLanes(
    items.filter((item) => segmentsFor(item, days, config).length > 0),
  );

  return placements.flatMap(({ item, lane, laneCount }) =>
    coalesceSegments(segmentsFor(item, days, config)).map(
      (run): TimelineBlock => ({
        event: item.event,
        lane,
        laneCount,
        leftFraction: run.startMin / totalMinutes,
        widthFraction: (run.endMin - run.startMin) / totalMinutes,
        allDay: item.event.allDay === true,
        continuesBefore: run.clippedStart,
        continuesAfter: run.clippedEnd,
      }),
    ),
  );
}

/** A resource's slot in the display order: its own row, or the group it anchors. */
type Slot = { kind: "group"; id: string } | { kind: "solo"; resource: CalendarResource };

/**
 * Build the full resource-timeline geometry.
 *
 * Rows follow the caller's `resources` order, except that a group's members are clustered
 * contiguously at the position where the group FIRST appears — so a group always reads as one block
 * even if the caller interleaved its members. An ungrouped resource keeps its own position.
 *
 * Duplicate resource ids are de-duplicated (first wins): a repeated id would otherwise render the
 * same row twice and make a drop target ambiguous.
 */
export function buildResourceTimeline(
  resources: readonly CalendarResource[],
  events: readonly CalendarEvent[],
  days: readonly string[],
  config: TimelineConfigInput = {},
): ResourceTimeline {
  const resolved = resolveWindow(config);
  const collapsedIds = new Set(config.collapsedGroupIds ?? []);

  const unique: CalendarResource[] = [];
  const knownIds = new Set<string>();
  for (const resource of resources) {
    if (knownIds.has(resource.id)) continue;
    knownIds.add(resource.id);
    unique.push(resource);
  }

  // Display order: a group anchors at its first member; ungrouped resources keep their place. A
  // blank `groupId` counts as ungrouped — a group with no label is meaningless.
  const slots: Slot[] = [];
  const members = new Map<string, CalendarResource[]>();
  for (const resource of unique) {
    const groupId = resource.groupId ? resource.groupId : undefined;
    if (groupId === undefined) {
      slots.push({ kind: "solo", resource });
      continue;
    }
    const existing = members.get(groupId);
    if (existing) {
      existing.push(resource);
    } else {
      members.set(groupId, [resource]);
      slots.push({ kind: "group", id: groupId });
    }
  }

  // Route every event to its row. An absent or unknown `resourceId` lands in the unassigned bucket
  // rather than being dropped.
  const byResource = new Map<string, RowItem[]>();
  const unassigned: RowItem[] = [];
  for (const event of events) {
    const item: RowItem = { event, ...timelineSpan(event) };
    const resourceId = event.resourceId;
    if (resourceId !== undefined && knownIds.has(resourceId)) {
      const bucket = byResource.get(resourceId);
      if (bucket) bucket.push(item);
      else byResource.set(resourceId, [item]);
    } else {
      unassigned.push(item);
    }
  }

  const makeRow = (
    resource: CalendarResource | null,
    groupId: string | null,
    rowItems: readonly RowItem[],
  ): TimelineRow => {
    const blocks = layoutRow(rowItems, days, resolved);
    return {
      resource,
      groupId,
      blocks,
      laneCount: blocks.reduce((max, block) => Math.max(max, block.laneCount), 1),
    };
  };

  const items: TimelineItem[] = [];
  for (const slot of slots) {
    if (slot.kind === "solo") {
      items.push({
        kind: "row",
        row: makeRow(slot.resource, null, byResource.get(slot.resource.id) ?? []),
      });
      continue;
    }
    const groupMembers = members.get(slot.id) ?? [];
    const collapsed = collapsedIds.has(slot.id);
    // The header renders either way; only its rows are hidden. A collapsed group's events are hidden
    // WITH it — re-homing them to the unassigned row would misreport whose they are.
    items.push({
      kind: "group",
      group: { id: slot.id, collapsed, resourceCount: groupMembers.length },
    });
    if (collapsed) continue;
    for (const resource of groupMembers) {
      items.push({
        kind: "row",
        row: makeRow(resource, slot.id, byResource.get(resource.id) ?? []),
      });
    }
  }

  // The unassigned row goes last, and only when it has something to show.
  const unassignedRow = makeRow(null, null, unassigned);
  if (unassignedRow.blocks.length > 0) items.push({ kind: "row", row: unassignedRow });

  const dayCount = days.length;
  const dayHeaders: TimelineDayHeader[] = days.map((dateOnly, index) => ({
    dateOnly,
    leftFraction: dayCount > 0 ? index / dayCount : 0,
    widthFraction: dayCount > 0 ? 1 / dayCount : 0,
  }));

  const totalMinutes = dayCount * resolved.windowMinutes;
  const ticks: TimelineTick[] = [];
  if (totalMinutes > 0) {
    days.forEach((dateOnly, index) => {
      const dayOrigin = index * resolved.windowMinutes;
      for (let hour = resolved.dayStartHour; hour < resolved.dayEndHour; hour += 1) {
        const offset = (hour - resolved.dayStartHour) * MINUTES_PER_HOUR;
        ticks.push({
          dateOnly,
          hour,
          leftFraction: (dayOrigin + offset) / totalMinutes,
          isDayStart: hour === resolved.dayStartHour,
        });
      }
    });
  }

  return { days: [...days], items, dayHeaders, ticks, config: resolved };
}

/**
 * Map a fraction [0, 1] ACROSS the timeline axis back to the day + snapped minute it represents —
 * the inverse of the `leftFraction` the geometry emits, and the horizontal counterpart of the time
 * grid's `fractionToMinuteOfDay`. The React layer turns a pointer's x into a fraction and calls this;
 * it never does axis maths itself (RF-23).
 *
 * Returns `null` for a degenerate axis (no days / zero-width window) rather than inventing a point.
 */
export function timelinePointAt(
  fraction: number,
  days: readonly string[],
  config: TimelineConfigInput = {},
  snapMinutes: number = DEFAULT_SNAP_MINUTES,
): GridPoint | null {
  const resolved = resolveWindow(config);
  if (days.length === 0 || resolved.windowMinutes <= 0) return null;

  const totalMinutes = days.length * resolved.windowMinutes;
  const axisMinutes = clamp(fraction, 0, 1) * totalMinutes;
  // Clamp the day index so a fraction of exactly 1 lands on the LAST day's end, not a phantom day.
  const index = Math.min(
    Math.floor(axisMinutes / resolved.windowMinutes),
    days.length - 1,
  );
  const withinDay = axisMinutes - index * resolved.windowMinutes;

  const windowStartMin = resolved.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = resolved.dayEndHour * MINUTES_PER_HOUR;
  const step = snapMinutes > 0 ? snapMinutes : DEFAULT_SNAP_MINUTES;
  // Snap relative to the WINDOW START (not midnight), so a step that does not divide the start hour
  // still lands fraction 0 exactly on the window's first slot.
  const snapped = windowStartMin + Math.round(withinDay / step) * step;

  return {
    dateOnly: days[index]!,
    minuteOfDay: clamp(snapped, windowStartMin, windowEndMin),
  };
}

/**
 * The fraction [0, 1] of the axis at which a "now" line should be drawn, or `null` when `now` falls
 * on a day the axis does not show, or outside the visible hour window. The horizontal counterpart of
 * the time grid's `nowMarkerFraction`.
 */
export function timelineNowFraction(
  now: Date,
  days: readonly string[],
  config: TimelineConfigInput = {},
): number | null {
  const resolved = resolveWindow(config);
  const index = days.indexOf(toDateOnly(formatLocalDateTime(now)));
  if (index === -1) return null;

  const minutes = now.getHours() * MINUTES_PER_HOUR + now.getMinutes() + now.getSeconds() / 60;
  const windowStartMin = resolved.dayStartHour * MINUTES_PER_HOUR;
  const windowEndMin = resolved.dayEndHour * MINUTES_PER_HOUR;
  // The window is half-open [start, end): a `now` exactly at the end boundary belongs to the next,
  // invisible slot and draws no line.
  if (minutes < windowStartMin || minutes >= windowEndMin) return null;

  const totalMinutes = days.length * resolved.windowMinutes;
  if (totalMinutes <= 0) return null;
  return (index * resolved.windowMinutes + (minutes - windowStartMin)) / totalMinutes;
}
