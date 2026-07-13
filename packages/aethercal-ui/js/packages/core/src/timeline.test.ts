import { describe, expect, it } from "vitest";
import { resolveTimeGridConfig } from "./timeGrid";
import {
  DEFAULT_TIMELINE_DAYS,
  MAX_TIMELINE_DAYS,
  buildResourceTimeline,
  resolveTimelineDays,
  timelineNowFraction,
} from "./timeline";
import type { CalendarEvent, CalendarResource } from "./types";

const D1 = "2026-07-13"; // Monday
const D2 = "2026-07-14";
const D3 = "2026-07-15";
const WEEK = [D1, D2, D3];

const HOSTS: CalendarResource[] = [
  { id: "h1", title: "Dr. Rivas" },
  { id: "h2", title: "Dr. Sosa" },
];

function ev(
  id: string,
  start: string,
  end: string,
  extra: Partial<CalendarEvent> = {},
): CalendarEvent {
  return { id, title: id, start, end, ...extra };
}

/** Every rendered resource row, in display order (group headers filtered out). */
function rowsOf(timeline: ReturnType<typeof buildResourceTimeline>) {
  return timeline.items.flatMap((item) => (item.kind === "row" ? [item.row] : []));
}

/** The row for a resource id (or the unassigned row when `id` is null). */
function rowFor(timeline: ReturnType<typeof buildResourceTimeline>, id: string | null) {
  return rowsOf(timeline).find((row) => (row.resource?.id ?? null) === id);
}

const groupsOf = (timeline: ReturnType<typeof buildResourceTimeline>) =>
  timeline.items.flatMap((item) => (item.kind === "group" ? [item.group] : []));

describe("buildResourceTimeline — rows", () => {
  it("emits one row per resource, in the caller's order", () => {
    const timeline = buildResourceTimeline(HOSTS, [], WEEK);
    expect(rowsOf(timeline).map((r) => r.resource?.id)).toEqual(["h1", "h2"]);
  });

  it("routes each event into the row its resourceId names", () => {
    const events = [
      ev("a", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h1" }),
      ev("b", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h2" }),
    ];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, "h1")?.blocks.map((b) => b.event.id)).toEqual(["a"]);
    expect(rowFor(timeline, "h2")?.blocks.map((b) => b.event.id)).toEqual(["b"]);
  });

  it("surfaces events with no resourceId in an unassigned row instead of dropping them", () => {
    const events = [ev("orphan", `${D1}T09:00:00`, `${D1}T10:00:00`)];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    const unassigned = rowFor(timeline, null);
    expect(unassigned).toBeDefined();
    expect(unassigned?.blocks.map((b) => b.event.id)).toEqual(["orphan"]);
  });

  it("treats an event whose resourceId matches no resource as unassigned (never silently lost)", () => {
    const events = [ev("ghost", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "nope" })];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, null)?.blocks.map((b) => b.event.id)).toEqual(["ghost"]);
  });

  it("renders no unassigned row when every visible event has a known resource", () => {
    const events = [ev("a", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h1" })];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, null)).toBeUndefined();
  });

  it("puts the unassigned row last, after every resource row", () => {
    const events = [ev("orphan", `${D1}T09:00:00`, `${D1}T10:00:00`)];
    const rows = rowsOf(buildResourceTimeline(HOSTS, events, WEEK));
    expect(rows[rows.length - 1]?.resource).toBeNull();
  });

  it("de-duplicates resources by id (first wins) so a row can never render twice", () => {
    const dupes: CalendarResource[] = [...HOSTS, { id: "h1", title: "Impostor" }];
    const rows = rowsOf(buildResourceTimeline(dupes, [], WEEK));
    expect(rows.map((r) => r.resource?.id)).toEqual(["h1", "h2"]);
    expect(rows[0]?.resource?.title).toBe("Dr. Rivas");
  });

  it("drops events that fall entirely outside the visible day range", () => {
    const events = [ev("far", "2026-08-01T09:00:00", "2026-08-01T10:00:00", { resourceId: "h1" })];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, "h1")?.blocks).toEqual([]);
  });
});

describe("buildResourceTimeline — horizontal geometry", () => {
  it("places an event at its fraction of a single-day axis", () => {
    // Noon on a full 0..24 window is exactly halfway across a one-day axis.
    const events = [ev("a", `${D1}T12:00:00`, `${D1}T18:00:00`, { resourceId: "h1" })];
    const timeline = buildResourceTimeline(HOSTS, events, [D1]);
    const block = rowFor(timeline, "h1")?.blocks[0];
    expect(block?.leftFraction).toBeCloseTo(0.5, 10);
    expect(block?.widthFraction).toBeCloseTo(0.25, 10); // 6h of 24h
  });

  it("offsets an event by its day index across a multi-day axis", () => {
    // Noon on day 2 of 3 => one full day (1/3) + half of the second day (0.5/3).
    const events = [ev("a", `${D2}T12:00:00`, `${D2}T12:00:00`, { resourceId: "h1" })];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, "h1")?.blocks[0]?.leftFraction).toBeCloseTo(1 / 3 + 0.5 / 3, 10);
  });

  it("positions against the VISIBLE hour window, not midnight", () => {
    // With an 8..18 window, 08:00 is the left edge and 13:00 is the midpoint of a 10h day.
    const events = [
      ev("a", `${D1}T08:00:00`, `${D1}T09:00:00`, { resourceId: "h1" }),
      ev("b", `${D1}T13:00:00`, `${D1}T14:00:00`, { resourceId: "h2" }),
    ];
    const timeline = buildResourceTimeline(HOSTS, events, [D1], {
      dayStartHour: 8,
      dayEndHour: 18,
    });
    expect(rowFor(timeline, "h1")?.blocks[0]?.leftFraction).toBeCloseTo(0, 10);
    expect(rowFor(timeline, "h2")?.blocks[0]?.leftFraction).toBeCloseTo(0.5, 10);
  });

  it("renders a continuous multi-day event as ONE coalesced bar, not one block per day", () => {
    // Crossing midnight must not produce a seam: the axis is continuous, so the bar is too.
    const events = [ev("a", `${D1}T22:00:00`, `${D2}T02:00:00`, { resourceId: "h1" })];
    const blocks = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1")?.blocks ?? [];
    expect(blocks).toHaveLength(1);
    // 22:00 of day 1 of 3 => (22/24)/3; 4 hours wide => (4/24)/3.
    expect(blocks[0]?.leftFraction).toBeCloseTo(22 / 24 / 3, 10);
    expect(blocks[0]?.widthFraction).toBeCloseTo(4 / 24 / 3, 10);
  });

  it("clips at the window edges and flags what continues beyond them", () => {
    // Starts before the visible range and ends after it: clipped on both sides.
    const events = [ev("a", "2026-07-10T09:00:00", "2026-07-20T09:00:00", { resourceId: "h1" })];
    const block = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1")?.blocks[0];
    expect(block?.leftFraction).toBeCloseTo(0, 10);
    expect(block?.widthFraction).toBeCloseTo(1, 10);
    expect(block?.continuesBefore).toBe(true);
    expect(block?.continuesAfter).toBe(true);
  });

  it("does not flag an event that fits entirely inside the window", () => {
    const events = [ev("a", `${D2}T09:00:00`, `${D2}T10:00:00`, { resourceId: "h1" })];
    const block = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1")?.blocks[0];
    expect(block?.continuesBefore).toBe(false);
    expect(block?.continuesAfter).toBe(false);
  });

  it("keeps every block inside the axis (0 <= left, left + width <= 1)", () => {
    const events = [
      ev("a", "2026-07-01T00:00:00", "2026-07-30T00:00:00", { resourceId: "h1" }),
      ev("b", `${D3}T23:00:00`, `${D3}T23:59:00`, { resourceId: "h2" }),
    ];
    for (const row of rowsOf(buildResourceTimeline(HOSTS, events, WEEK))) {
      for (const block of row.blocks) {
        expect(block.leftFraction).toBeGreaterThanOrEqual(0);
        expect(block.leftFraction + block.widthFraction).toBeLessThanOrEqual(1 + 1e-9);
      }
    }
  });
});

describe("buildResourceTimeline — all-day events", () => {
  it("spans an all-day event across the full width of the day it occupies", () => {
    const events = [
      ev("a", `${D2}T00:00:00`, `${D3}T00:00:00`, { resourceId: "h1", allDay: true }),
    ];
    const block = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1")?.blocks[0];
    expect(block?.allDay).toBe(true);
    expect(block?.leftFraction).toBeCloseTo(1 / 3, 10);
    expect(block?.widthFraction).toBeCloseTo(1 / 3, 10);
  });

  it("still covers its one day when start === end (a zero-length all-day event)", () => {
    // occupiedDayBounds treats a zero-duration event as occupying its start day; a raw time span
    // would collapse it to a zero-width sliver and make it invisible.
    const events = [
      ev("a", `${D2}T00:00:00`, `${D2}T00:00:00`, { resourceId: "h1", allDay: true }),
    ];
    const block = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1")?.blocks[0];
    expect(block?.widthFraction).toBeCloseTo(1 / 3, 10);
  });

  it("lane-stacks an all-day bar against a timed event of the same resource", () => {
    // Unlike week/day (which need a separate all-day rail because a vertical time axis cannot
    // express "no time"), a horizontal day-spanning axis renders an all-day event natively.
    const events = [
      ev("allday", `${D1}T00:00:00`, `${D2}T00:00:00`, { resourceId: "h1", allDay: true }),
      ev("timed", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h1" }),
    ];
    const row = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1");
    expect(row?.blocks).toHaveLength(2);
    expect(row?.laneCount).toBe(2);
    expect(new Set(row?.blocks.map((b) => b.lane))).toEqual(new Set([0, 1]));
  });
});

describe("buildResourceTimeline — lane packing within a row", () => {
  it("gives overlapping events of the same resource different lanes", () => {
    const events = [
      ev("a", `${D1}T09:00:00`, `${D1}T11:00:00`, { resourceId: "h1" }),
      ev("b", `${D1}T10:00:00`, `${D1}T12:00:00`, { resourceId: "h1" }),
    ];
    const row = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1");
    expect(row?.laneCount).toBe(2);
    expect(new Set(row?.blocks.map((b) => b.lane))).toEqual(new Set([0, 1]));
  });

  it("collapses sequential events of the same resource onto a single lane", () => {
    const events = [
      ev("a", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h1" }),
      ev("b", `${D1}T10:00:00`, `${D1}T11:00:00`, { resourceId: "h1" }),
    ];
    const row = rowFor(buildResourceTimeline(HOSTS, events, WEEK), "h1");
    expect(row?.laneCount).toBe(1);
    expect(row?.blocks.every((b) => b.lane === 0)).toBe(true);
  });

  it("packs each row independently — a busy resource never widens a quiet one", () => {
    const events = [
      ev("a", `${D1}T09:00:00`, `${D1}T11:00:00`, { resourceId: "h1" }),
      ev("b", `${D1}T09:30:00`, `${D1}T11:30:00`, { resourceId: "h1" }),
      ev("c", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "h2" }),
    ];
    const timeline = buildResourceTimeline(HOSTS, events, WEEK);
    expect(rowFor(timeline, "h1")?.laneCount).toBe(2);
    expect(rowFor(timeline, "h2")?.laneCount).toBe(1); // untouched by h1's overlap
  });
});

describe("buildResourceTimeline — grouping and collapse", () => {
  const GROUPED: CalendarResource[] = [
    { id: "a1", title: "Room A1", groupId: "Clinic A" },
    { id: "b1", title: "Room B1", groupId: "Clinic B" },
    { id: "a2", title: "Room A2", groupId: "Clinic A" },
    { id: "solo", title: "Mobile unit" },
  ];

  it("clusters a group's resources contiguously at the group's first appearance", () => {
    const timeline = buildResourceTimeline(GROUPED, [], WEEK);
    const order = timeline.items.map((item) =>
      item.kind === "group" ? `#${item.group.id}` : (item.row.resource?.id ?? "unassigned"),
    );
    // "Clinic A" anchors where a1 appeared, and a2 joins it there rather than staying after b1.
    expect(order).toEqual(["#Clinic A", "a1", "a2", "#Clinic B", "b1", "solo"]);
  });

  it("reports each group's resource count", () => {
    const groups = groupsOf(buildResourceTimeline(GROUPED, [], WEEK));
    expect(groups.map((g) => [g.id, g.resourceCount])).toEqual([
      ["Clinic A", 2],
      ["Clinic B", 1],
    ]);
  });

  it("keeps an ungrouped resource out of every group", () => {
    const timeline = buildResourceTimeline(GROUPED, [], WEEK);
    expect(rowFor(timeline, "solo")?.groupId).toBeNull();
    expect(rowFor(timeline, "a1")?.groupId).toBe("Clinic A");
  });

  it("hides a collapsed group's rows but still renders its header", () => {
    const timeline = buildResourceTimeline(GROUPED, [], WEEK, {
      collapsedGroupIds: ["Clinic A"],
    });
    expect(rowsOf(timeline).map((r) => r.resource?.id)).toEqual(["b1", "solo"]);
    const collapsed = groupsOf(timeline).find((g) => g.id === "Clinic A");
    expect(collapsed?.collapsed).toBe(true);
    expect(collapsed?.resourceCount).toBe(2); // the header still reports what it hides
  });

  it("hides a collapsed group's events WITH its rows (never re-homes them as unassigned)", () => {
    // A collapsed event is hidden, not orphaned — moving it to the unassigned row would be a lie.
    const events = [ev("a", `${D1}T09:00:00`, `${D1}T10:00:00`, { resourceId: "a1" })];
    const timeline = buildResourceTimeline(GROUPED, events, WEEK, {
      collapsedGroupIds: ["Clinic A"],
    });
    expect(rowFor(timeline, null)).toBeUndefined();
    expect(rowsOf(timeline).flatMap((r) => r.blocks)).toEqual([]);
  });

  it("treats a blank groupId as ungrouped (a group with no label is meaningless)", () => {
    const resources: CalendarResource[] = [{ id: "x", title: "X", groupId: "" }];
    const timeline = buildResourceTimeline(resources, [], WEEK);
    expect(groupsOf(timeline)).toEqual([]);
    expect(rowFor(timeline, "x")?.groupId).toBeNull();
  });
});

describe("buildResourceTimeline — axis furniture", () => {
  it("emits one evenly-sized day header per visible day", () => {
    const { dayHeaders } = buildResourceTimeline(HOSTS, [], WEEK);
    expect(dayHeaders.map((d) => d.dateOnly)).toEqual(WEEK);
    expect(dayHeaders[0]?.leftFraction).toBeCloseTo(0, 10);
    expect(dayHeaders[1]?.leftFraction).toBeCloseTo(1 / 3, 10);
    expect(dayHeaders.every((d) => Math.abs(d.widthFraction - 1 / 3) < 1e-9)).toBe(true);
  });

  it("emits an hour tick per visible hour per day, flagging each day boundary", () => {
    const { ticks } = buildResourceTimeline(HOSTS, [], [D1, D2], {
      dayStartHour: 9,
      dayEndHour: 12,
    });
    expect(ticks).toHaveLength(6); // 3 hours x 2 days
    expect(ticks.filter((t) => t.isDayStart).map((t) => t.dateOnly)).toEqual([D1, D2]);
    expect(ticks[0]).toMatchObject({ dateOnly: D1, hour: 9, leftFraction: 0 });
    expect(ticks[3]).toMatchObject({ dateOnly: D2, hour: 9 });
    expect(ticks[3]?.leftFraction).toBeCloseTo(0.5, 10);
  });

  it("resolves the hour window like the time grid (an inverted window degrades to the full day)", () => {
    const { config } = buildResourceTimeline(HOSTS, [], WEEK, {
      dayStartHour: 18,
      dayEndHour: 8,
    });
    expect(config).toEqual(resolveTimeGridConfig());
  });
});

describe("timelineNowFraction", () => {
  it("places 'now' at its fraction of the axis when the day is visible", () => {
    const now = new Date(2026, 6, 14, 12, 0, 0); // noon on D2, day 2 of 3
    expect(timelineNowFraction(now, WEEK, resolveTimeGridConfig())).toBeCloseTo(
      1 / 3 + 0.5 / 3,
      10,
    );
  });

  it("returns null when 'now' falls on a day the axis does not show", () => {
    const now = new Date(2026, 7, 1, 12, 0, 0);
    expect(timelineNowFraction(now, WEEK, resolveTimeGridConfig())).toBeNull();
  });

  it("returns null when 'now' falls outside the visible hour window", () => {
    const now = new Date(2026, 6, 14, 6, 0, 0); // 06:00, before an 8..18 window
    const config = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 });
    expect(timelineNowFraction(now, WEEK, config)).toBeNull();
  });
});

describe("resolveTimelineDays", () => {
  it("defaults to a week", () => {
    expect(resolveTimelineDays()).toBe(DEFAULT_TIMELINE_DAYS);
    expect(DEFAULT_TIMELINE_DAYS).toBe(7);
  });

  it("clamps a hostile value from a plain-JS consumer into the supported range", () => {
    expect(resolveTimelineDays(0)).toBe(1);
    expect(resolveTimelineDays(-5)).toBe(1);
    expect(resolveTimelineDays(9999)).toBe(MAX_TIMELINE_DAYS);
    expect(resolveTimelineDays(Number.NaN)).toBe(DEFAULT_TIMELINE_DAYS);
    expect(resolveTimelineDays(3.7)).toBe(3); // truncated, never fractional
  });
});
