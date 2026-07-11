import { describe, expect, it } from "vitest";
import type { CalendarEvent } from "./types";
import {
  buildTimeGrid,
  layoutDayColumn,
  nowMarkerFraction,
  resolveTimeGridConfig,
  splitAllDay,
} from "./timeGrid";

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start" | "end">): CalendarEvent {
  return { title: partial.title ?? partial.id, ...partial };
}

const FULL_DAY = resolveTimeGridConfig();

describe("resolveTimeGridConfig", () => {
  it("defaults to a full 0..24 day (1440-minute window)", () => {
    expect(FULL_DAY).toEqual({ dayStartHour: 0, dayEndHour: 24, windowMinutes: 1440 });
  });

  it("honors a narrowed business-hours window", () => {
    expect(resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 })).toEqual({
      dayStartHour: 8,
      dayEndHour: 18,
      windowMinutes: 600,
    });
  });

  it("clamps an inverted or out-of-range window back to something sane", () => {
    const cfg = resolveTimeGridConfig({ dayStartHour: 20, dayEndHour: 4 });
    expect(cfg.dayStartHour).toBeLessThan(cfg.dayEndHour);
    expect(cfg.windowMinutes).toBeGreaterThan(0);
  });
});

describe("splitAllDay", () => {
  it("separates all-day events from timed ones", () => {
    const events = [
      evt({ id: "timed", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "allday", start: "2026-07-15", end: "2026-07-15", allDay: true }),
    ];
    const { allDay, timed } = splitAllDay(events);
    expect(allDay.map((e) => e.id)).toEqual(["allday"]);
    expect(timed.map((e) => e.id)).toEqual(["timed"]);
  });

  it("treats a missing allDay flag as timed (default false)", () => {
    const { allDay, timed } = splitAllDay([
      evt({ id: "x", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
    ]);
    expect(allDay).toHaveLength(0);
    expect(timed).toHaveLength(1);
  });
});

describe("layoutDayColumn — vertical geometry", () => {
  it("positions a 09:00–10:00 event by fraction of the day window", () => {
    const blocks = layoutDayColumn(
      [evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" })],
      "2026-07-15",
      FULL_DAY,
    );
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.topFraction).toBeCloseTo(9 / 24);
    expect(blocks[0]!.heightFraction).toBeCloseTo(1 / 24);
    expect(blocks[0]!.lane).toBe(0);
    expect(blocks[0]!.laneCount).toBe(1);
  });

  it("clamps an event that starts before the window to the top", () => {
    const cfg = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 24 });
    const blocks = layoutDayColumn(
      [evt({ id: "early", start: "2026-07-15T07:00:00", end: "2026-07-15T09:00:00" })],
      "2026-07-15",
      cfg,
    );
    expect(blocks[0]!.topFraction).toBe(0);
    // 08:00 -> 09:00 visible = 60 min of a 960-min window.
    expect(blocks[0]!.heightFraction).toBeCloseTo(60 / 960);
  });

  it("clamps an event that crosses midnight to the bottom of the window", () => {
    const blocks = layoutDayColumn(
      [evt({ id: "late", start: "2026-07-15T23:00:00", end: "2026-07-16T01:00:00" })],
      "2026-07-15",
      FULL_DAY,
    );
    expect(blocks[0]!.topFraction).toBeCloseTo(23 / 24);
    // clamped bottom is 24:00 -> visible height is the final hour only.
    expect(blocks[0]!.topFraction + blocks[0]!.heightFraction).toBeCloseTo(1);
    expect(blocks[0]!.heightFraction).toBeCloseTo(1 / 24);
  });
});

describe("layoutDayColumn — DST-safe vertical geometry (TZ America/New_York)", () => {
  it("positions by wall-clock time on a spring-forward day (23-hour day)", () => {
    // 2026-03-08 is the US spring-forward date (02:00 -> 03:00). A 09:00 event must still sit at
    // 9/24, not 8/24 — the skipped hour must not shift the block (component-based, not timestamp).
    const blocks = layoutDayColumn(
      [evt({ id: "dst", start: "2026-03-08T09:00:00", end: "2026-03-08T10:00:00" })],
      "2026-03-08",
      FULL_DAY,
    );
    expect(blocks[0]!.topFraction).toBeCloseTo(9 / 24);
    expect(blocks[0]!.heightFraction).toBeCloseTo(1 / 24);
  });

  it("positions by wall-clock time on a fall-back day (25-hour day)", () => {
    // 2026-11-01 is the US fall-back date (02:00 -> 01:00). A 09:00 event must still sit at 9/24,
    // not 10/24 — the repeated hour must not shift the block either.
    const blocks = layoutDayColumn(
      [evt({ id: "dst", start: "2026-11-01T09:00:00", end: "2026-11-01T10:00:00" })],
      "2026-11-01",
      FULL_DAY,
    );
    expect(blocks[0]!.topFraction).toBeCloseTo(9 / 24);
    expect(blocks[0]!.heightFraction).toBeCloseTo(1 / 24);
  });
});

describe("layoutDayColumn — events outside a narrowed window", () => {
  const BUSINESS = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 });

  it("drops an event that ends at or before the window start", () => {
    const blocks = layoutDayColumn(
      [evt({ id: "early", start: "2026-07-15T06:00:00", end: "2026-07-15T07:00:00" })],
      "2026-07-15",
      BUSINESS,
    );
    expect(blocks).toHaveLength(0);
  });

  it("drops an event that starts at or after the window end", () => {
    const blocks = layoutDayColumn(
      [evt({ id: "late", start: "2026-07-15T19:00:00", end: "2026-07-15T20:00:00" })],
      "2026-07-15",
      BUSINESS,
    );
    expect(blocks).toHaveLength(0);
  });

  it("keeps (and does not let an out-of-window event steal a lane from) a visible event", () => {
    const blocks = layoutDayColumn(
      [
        evt({ id: "before", start: "2026-07-15T06:00:00", end: "2026-07-15T07:30:00" }),
        evt({ id: "visible", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      ],
      "2026-07-15",
      BUSINESS,
    );
    expect(blocks.map((b) => b.event.id)).toEqual(["visible"]);
    // The dropped out-of-window event must not squeeze the visible one into a half-width lane.
    expect(blocks[0]!.laneCount).toBe(1);
  });
});

describe("layoutDayColumn — overlap lanes", () => {
  it("keeps two adjacent (touching, non-overlapping) events in a single lane", () => {
    const blocks = layoutDayColumn(
      [
        evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
        evt({ id: "b", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" }),
      ],
      "2026-07-15",
      FULL_DAY,
    );
    expect(blocks.every((b) => b.lane === 0)).toBe(true);
    expect(blocks.every((b) => b.laneCount === 1)).toBe(true);
  });

  it("splits two overlapping events into two lanes", () => {
    const blocks = layoutDayColumn(
      [
        evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T10:30:00" }),
        evt({ id: "b", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" }),
      ],
      "2026-07-15",
      FULL_DAY,
    );
    const byId = new Map(blocks.map((b) => [b.event.id, b]));
    expect(byId.get("a")!.laneCount).toBe(2);
    expect(byId.get("b")!.laneCount).toBe(2);
    expect(new Set(blocks.map((b) => b.lane))).toEqual(new Set([0, 1]));
  });

  it("gives three mutually overlapping events three lanes", () => {
    const blocks = layoutDayColumn(
      [
        evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T12:00:00" }),
        evt({ id: "b", start: "2026-07-15T09:30:00", end: "2026-07-15T12:00:00" }),
        evt({ id: "c", start: "2026-07-15T10:00:00", end: "2026-07-15T12:00:00" }),
      ],
      "2026-07-15",
      FULL_DAY,
    );
    expect(blocks.every((b) => b.laneCount === 3)).toBe(true);
    expect(new Set(blocks.map((b) => b.lane))).toEqual(new Set([0, 1, 2]));
  });

  it("resets the lane count after a gap (separate overlap clusters)", () => {
    const blocks = layoutDayColumn(
      [
        evt({ id: "a", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
        evt({ id: "b", start: "2026-07-15T09:30:00", end: "2026-07-15T10:30:00" }),
        evt({ id: "c", start: "2026-07-15T14:00:00", end: "2026-07-15T15:00:00" }),
      ],
      "2026-07-15",
      FULL_DAY,
    );
    const byId = new Map(blocks.map((b) => [b.event.id, b]));
    expect(byId.get("a")!.laneCount).toBe(2);
    expect(byId.get("b")!.laneCount).toBe(2);
    expect(byId.get("c")!.laneCount).toBe(1);
    expect(byId.get("c")!.lane).toBe(0);
  });
});

describe("buildTimeGrid — columns per day", () => {
  const week = [
    "2026-07-13",
    "2026-07-14",
    "2026-07-15",
    "2026-07-16",
    "2026-07-17",
    "2026-07-18",
    "2026-07-19",
  ];

  it("produces one column per requested day, in order", () => {
    const grid = buildTimeGrid(week, [], FULL_DAY);
    expect(grid.columns.map((c) => c.dateOnly)).toEqual(week);
  });

  it("routes each timed event into the column of its start day", () => {
    const grid = buildTimeGrid(
      week,
      [
        evt({ id: "mon", start: "2026-07-13T09:00:00", end: "2026-07-13T10:00:00" }),
        evt({ id: "wed", start: "2026-07-15T14:00:00", end: "2026-07-15T15:00:00" }),
      ],
      FULL_DAY,
    );
    const colOf = (d: string) => grid.columns.find((c) => c.dateOnly === d)!;
    expect(colOf("2026-07-13").timed.map((b) => b.event.id)).toEqual(["mon"]);
    expect(colOf("2026-07-15").timed.map((b) => b.event.id)).toEqual(["wed"]);
    expect(colOf("2026-07-14").timed).toHaveLength(0);
  });

  it("routes all-day events into the column's all-day bucket, never the time grid", () => {
    const grid = buildTimeGrid(
      week,
      [evt({ id: "holiday", start: "2026-07-15", end: "2026-07-15", allDay: true })],
      FULL_DAY,
    );
    const col = grid.columns.find((c) => c.dateOnly === "2026-07-15")!;
    expect(col.allDay.map((e) => e.id)).toEqual(["holiday"]);
    expect(col.timed).toHaveLength(0);
  });

  it("omits events whose start day is outside the visible columns", () => {
    const grid = buildTimeGrid(
      week,
      [evt({ id: "next-week", start: "2026-07-27T09:00:00", end: "2026-07-27T10:00:00" })],
      FULL_DAY,
    );
    expect(grid.columns.flatMap((c) => c.timed)).toHaveLength(0);
  });

  it("renders a cross-midnight timed event in every day it overlaps (clamped per column)", () => {
    const grid = buildTimeGrid(
      ["2026-07-15", "2026-07-16"],
      [evt({ id: "overnight", start: "2026-07-15T23:00:00", end: "2026-07-16T01:00:00" })],
      FULL_DAY,
    );
    const d15 = grid.columns.find((c) => c.dateOnly === "2026-07-15")!;
    const d16 = grid.columns.find((c) => c.dateOnly === "2026-07-16")!;
    expect(d15.timed.map((b) => b.event.id)).toEqual(["overnight"]);
    expect(d16.timed.map((b) => b.event.id)).toEqual(["overnight"]);
    // Day 1 shows the last hour (23:00 → midnight); day 2 shows the first hour (midnight → 01:00).
    expect(d15.timed[0]!.topFraction).toBeCloseTo(23 / 24);
    expect(d15.timed[0]!.topFraction + d15.timed[0]!.heightFraction).toBeCloseTo(1);
    expect(d16.timed[0]!.topFraction).toBeCloseTo(0);
    expect(d16.timed[0]!.heightFraction).toBeCloseTo(1 / 24);
  });

  it("spans a multi-day all-day event over its occupied days, treating the end as EXCLUSIVE (matches buildAgenda)", () => {
    // `end: "2026-07-17"` is the 17th's local midnight and is EXCLUSIVE, so the event occupies the
    // 15th and 16th only — NOT the 17th. This mirrors occupiedDays/buildAgenda (see agenda.test.ts
    // "treats an exclusive midnight end as not occupying the end day") so the week/day grid and the
    // agenda draw the SAME span for the same event.
    const grid = buildTimeGrid(
      ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-18"],
      [evt({ id: "trip", start: "2026-07-15", end: "2026-07-17", allDay: true })],
      FULL_DAY,
    );
    const covers = (d: string) =>
      grid.columns.find((c) => c.dateOnly === d)!.allDay.some((e) => e.id === "trip");
    expect(covers("2026-07-14")).toBe(false);
    expect(covers("2026-07-15")).toBe(true);
    expect(covers("2026-07-16")).toBe(true);
    expect(covers("2026-07-17")).toBe(false); // exclusive end day — not occupied
    expect(covers("2026-07-18")).toBe(false);
  });

  it("excludes the exclusive midnight end day of an all-day event (explicit datetime form)", () => {
    // Canonical all-day encoding (explicit midnight end): a 3-day conference on the 15th–17th ends
    // exclusively at the 18th's midnight, so it covers 15/16/17 but not 18 — the same result agenda
    // gives for the identical event.
    const grid = buildTimeGrid(
      ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-18"],
      [evt({ id: "conf", start: "2026-07-15T00:00:00", end: "2026-07-18T00:00:00", allDay: true })],
      FULL_DAY,
    );
    const covers = (d: string) =>
      grid.columns.find((c) => c.dateOnly === d)!.allDay.some((e) => e.id === "conf");
    expect(covers("2026-07-15")).toBe(true);
    expect(covers("2026-07-16")).toBe(true);
    expect(covers("2026-07-17")).toBe(true);
    expect(covers("2026-07-18")).toBe(false); // exclusive end — not occupied
  });

  it("keeps a single-day all-day event (start === end) on exactly its one day", () => {
    const grid = buildTimeGrid(
      ["2026-07-14", "2026-07-15", "2026-07-16"],
      [evt({ id: "holiday", start: "2026-07-15", end: "2026-07-15", allDay: true })],
      FULL_DAY,
    );
    const covers = (d: string) =>
      grid.columns.find((c) => c.dateOnly === d)!.allDay.some((e) => e.id === "holiday");
    expect(covers("2026-07-14")).toBe(false);
    expect(covers("2026-07-15")).toBe(true);
    expect(covers("2026-07-16")).toBe(false);
  });

  it("supports a single-day grid (the day view is a 1-column week)", () => {
    const grid = buildTimeGrid(["2026-07-15"], [], FULL_DAY);
    expect(grid.columns).toHaveLength(1);
    expect(grid.columns[0]!.dateOnly).toBe("2026-07-15");
  });

  it("exposes one hour mark per hour of the window", () => {
    const grid = buildTimeGrid(["2026-07-15"], [], FULL_DAY);
    expect(grid.hourMarks).toHaveLength(24);
    expect(grid.hourMarks[9]!.hour).toBe(9);
    expect(grid.hourMarks[9]!.topFraction).toBeCloseTo(9 / 24);
  });
});

describe("buildTimeGrid — cross-midnight continuation flags on timed blocks", () => {
  it("flags the start, pass-through, and final day of a multi-day timed event (mirrors AgendaEntry)", () => {
    // A 23:00 → 01:00-two-days-later event occupies three local days. Each column's block must know
    // WHERE in the span it sits, so the renderer can show the start time on the start day and an
    // honest continuation label (not a stale start time) on the later days — the same isContinuation
    // / continuesAfter semantics buildAgenda emits (agenda.test.ts "spans a timed event ... flags").
    const grid = buildTimeGrid(
      ["2026-07-15", "2026-07-16", "2026-07-17"],
      [evt({ id: "long", start: "2026-07-15T23:00:00", end: "2026-07-17T01:00:00" })],
      FULL_DAY,
    );
    const flagsOn = (d: string) => {
      const b = grid.columns.find((c) => c.dateOnly === d)!.timed.find((x) => x.event.id === "long")!;
      return [b.isContinuation, b.continuesAfter];
    };
    expect(flagsOn("2026-07-15")).toEqual([false, true]); // start day, continues after
    expect(flagsOn("2026-07-16")).toEqual([true, true]); // full pass-through day
    expect(flagsOn("2026-07-17")).toEqual([true, false]); // final day, ends here
  });

  it("leaves a single-day timed event unflagged (neither a continuation nor continuing)", () => {
    const grid = buildTimeGrid(
      ["2026-07-15"],
      [evt({ id: "solo", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" })],
      FULL_DAY,
    );
    const b = grid.columns[0]!.timed.find((x) => x.event.id === "solo")!;
    expect([b.isContinuation, b.continuesAfter]).toEqual([false, false]);
  });

  it("treats an event ending exactly at midnight as NOT continuing into the next day (exclusive end)", () => {
    // start 22:00 → end next-day 00:00 (exclusive). The start day is the only occupied day, so its
    // block does not continue after; the next day gets no block at all.
    const grid = buildTimeGrid(
      ["2026-07-15", "2026-07-16"],
      [evt({ id: "toMidnight", start: "2026-07-15T22:00:00", end: "2026-07-16T00:00:00" })],
      FULL_DAY,
    );
    const day15 = grid.columns.find((c) => c.dateOnly === "2026-07-15")!;
    const day16 = grid.columns.find((c) => c.dateOnly === "2026-07-16")!;
    const b15 = day15.timed.find((x) => x.event.id === "toMidnight")!;
    expect([b15.isContinuation, b15.continuesAfter]).toEqual([false, false]);
    expect(day16.timed.some((x) => x.event.id === "toMidnight")).toBe(false);
  });
});

describe("nowMarkerFraction", () => {
  it("maps a wall-clock time to its fraction of the window", () => {
    const noon = new Date(2026, 6, 15, 12, 0, 0);
    expect(nowMarkerFraction(noon, FULL_DAY)).toBeCloseTo(0.5);
  });

  it("returns null when now is before the window", () => {
    const early = new Date(2026, 6, 15, 6, 0, 0);
    expect(nowMarkerFraction(early, resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 }))).toBeNull();
  });

  it("returns null when now is after the window", () => {
    const late = new Date(2026, 6, 15, 20, 0, 0);
    expect(nowMarkerFraction(late, resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 }))).toBeNull();
  });

  it("returns null exactly at the exclusive end hour of a narrowed window", () => {
    const atEnd = new Date(2026, 6, 15, 18, 0, 0);
    expect(nowMarkerFraction(atEnd, resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 }))).toBeNull();
  });

  it("still draws just before the exclusive end", () => {
    const almostEnd = new Date(2026, 6, 15, 17, 59, 0);
    const cfg = resolveTimeGridConfig({ dayStartHour: 8, dayEndHour: 18 });
    expect(nowMarkerFraction(almostEnd, cfg)).toBeCloseTo((17 * 60 + 59 - 8 * 60) / 600);
  });
});
