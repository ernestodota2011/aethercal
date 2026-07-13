/**
 * Headless period-navigation geometry (F2-NAV).
 *
 * `getVisibleRange` answers "which period is on screen" (the payload for on_range_change /
 * on_view_change) and `stepAnchor` moves the anchor one period at a time (prev/next). Both are pure,
 * dependency-free, and DST-safe by construction (component-based date math), like the rest of the
 * core — the "what period" geometry lives here, never in the React layer or a consumer (RF-23).
 */
import { describe, expect, it } from "vitest";
import { getTimelineGridDays, parseLocalDateTime } from "./dateMath";
import { getVisibleRange, stepAnchor } from "./navigation";

describe("getVisibleRange", () => {
  it("month spans the calendar month, from the 1st to the exclusive 1st of next month", () => {
    const r = getVisibleRange("month", new Date(2026, 6, 15), 1); // 2026-07-15
    expect(r).toEqual({ view: "month", from: "2026-07-01T00:00:00", to: "2026-08-01T00:00:00" });
  });

  it("month crosses the year boundary in December", () => {
    const r = getVisibleRange("month", new Date(2026, 11, 10), 1);
    expect(r).toEqual({ view: "month", from: "2026-12-01T00:00:00", to: "2027-01-01T00:00:00" });
  });

  it("week spans Monday..next-Monday (exclusive) for a Monday-first week", () => {
    // 2026-07-15 is a Wednesday; the Monday-first week is 2026-07-13 .. 2026-07-20.
    const r = getVisibleRange("week", new Date(2026, 6, 15), 1);
    expect(r).toEqual({ view: "week", from: "2026-07-13T00:00:00", to: "2026-07-20T00:00:00" });
  });

  it("week honors a Sunday-first firstDayOfWeek", () => {
    const r = getVisibleRange("week", new Date(2026, 6, 15), 0);
    expect(r).toEqual({ view: "week", from: "2026-07-12T00:00:00", to: "2026-07-19T00:00:00" });
  });

  it("day spans a single day to the exclusive next midnight, ignoring the time-of-day", () => {
    const r = getVisibleRange("day", new Date(2026, 6, 15, 14, 30), 1);
    expect(r).toEqual({ view: "day", from: "2026-07-15T00:00:00", to: "2026-07-16T00:00:00" });
  });

  it("list spans the calendar month, like month (agenda over the visible month)", () => {
    const r = getVisibleRange("list", new Date(2026, 6, 15), 1);
    expect(r).toEqual({ view: "list", from: "2026-07-01T00:00:00", to: "2026-08-01T00:00:00" });
  });

  it("defaults to a Monday-first week when firstDayOfWeek is omitted", () => {
    const r = getVisibleRange("week", new Date(2026, 6, 15));
    expect(r.from).toBe("2026-07-13T00:00:00");
  });

  it("timeline spans N days starting AT the anchor (not week-aligned)", () => {
    // A configurable N-day window can only be week-aligned when N is 7; anchoring at the anchor day
    // is what makes an arbitrary N coherent — and keeps `from` a valid anchor (round-trip below).
    const r = getVisibleRange("timeline", new Date(2026, 6, 15), 1, 3);
    expect(r).toEqual({
      view: "timeline",
      from: "2026-07-15T00:00:00",
      to: "2026-07-18T00:00:00",
    });
  });

  it("timeline defaults to a 7-day window", () => {
    const r = getVisibleRange("timeline", new Date(2026, 6, 15), 1);
    expect(r).toEqual({
      view: "timeline",
      from: "2026-07-15T00:00:00",
      to: "2026-07-22T00:00:00",
    });
  });

  it("timeline clamps a hostile day count instead of building a degenerate axis", () => {
    expect(getVisibleRange("timeline", new Date(2026, 6, 15), 1, 0).to).toBe("2026-07-16T00:00:00");
    expect(getVisibleRange("timeline", new Date(2026, 6, 15), 1, 9999).to).toBe(
      "2026-08-15T00:00:00", // clamped to 31 days
    );
  });
});

describe("getTimelineGridDays", () => {
  it("returns N consecutive day keys starting at the anchor", () => {
    expect(getTimelineGridDays(new Date(2026, 6, 15), 3)).toEqual([
      "2026-07-15",
      "2026-07-16",
      "2026-07-17",
    ]);
  });

  it("crosses a month boundary (component-based, never a raw +24h)", () => {
    expect(getTimelineGridDays(new Date(2026, 6, 30), 3)).toEqual([
      "2026-07-30",
      "2026-07-31",
      "2026-08-01",
    ]);
  });
});

describe("stepAnchor", () => {
  it("month +1 lands on the first of the next month with no day-overflow (Jan 31 -> Feb 1)", () => {
    const next = stepAnchor(new Date(2026, 0, 31), "month", 1);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 1, 1]);
  });

  it("month -1 lands on the first of the previous month, crossing the year", () => {
    const prev = stepAnchor(new Date(2026, 0, 15), "month", -1);
    expect([prev.getFullYear(), prev.getMonth(), prev.getDate()]).toEqual([2025, 11, 1]);
  });

  it("week ±1 moves by exactly 7 calendar days", () => {
    const next = stepAnchor(new Date(2026, 6, 15), "week", 1);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 6, 22]);
    const prev = stepAnchor(new Date(2026, 6, 15), "week", -1);
    expect([prev.getFullYear(), prev.getMonth(), prev.getDate()]).toEqual([2026, 6, 8]);
  });

  it("day ±1 moves by one day, crossing a month boundary", () => {
    const next = stepAnchor(new Date(2026, 6, 31), "day", 1);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 7, 1]);
  });

  it("list steps by a month, like the month view", () => {
    const next = stepAnchor(new Date(2026, 6, 15), "list", 1);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 7, 1]);
  });

  it("timeline ±1 moves by exactly one window (N days), not one week", () => {
    const next = stepAnchor(new Date(2026, 6, 15), "timeline", 1, 3);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 6, 18]);
    const prev = stepAnchor(new Date(2026, 6, 15), "timeline", -1, 3);
    expect([prev.getFullYear(), prev.getMonth(), prev.getDate()]).toEqual([2026, 6, 12]);
  });

  it("timeline steps a 7-day window by default, so prev/next tile without gaps or overlap", () => {
    const next = stepAnchor(new Date(2026, 6, 15), "timeline", 1);
    expect([next.getFullYear(), next.getMonth(), next.getDate()]).toEqual([2026, 6, 22]);
  });

  it("re-anchoring on the emitted `from` reproduces the same range (controlled round-trip)", () => {
    // The controlled contract: the consumer sets anchor = payload.from; recomputing must be a no-op.
    for (const view of ["month", "week", "day", "list", "timeline"] as const) {
      const stepped = stepAnchor(new Date(2026, 6, 15), view, 1);
      const range = getVisibleRange(view, stepped, 1);
      const reanchored = getVisibleRange(view, parseLocalDateTime(range.from), 1);
      expect(reanchored).toEqual(range);
    }
  });

  it("timeline's round-trip holds for any window size (from is always a valid anchor)", () => {
    for (const days of [1, 3, 7, 14, 31]) {
      const stepped = stepAnchor(new Date(2026, 6, 15), "timeline", 1, days);
      const range = getVisibleRange("timeline", stepped, 1, days);
      const reanchored = getVisibleRange("timeline", parseLocalDateTime(range.from), 1, days);
      expect(reanchored).toEqual(range);
    }
  });

  it("stepping the timeline forward then back returns to the original anchor", () => {
    const start = new Date(2026, 6, 15);
    const round = stepAnchor(stepAnchor(start, "timeline", 1, 5), "timeline", -1, 5);
    expect([round.getFullYear(), round.getMonth(), round.getDate()]).toEqual([2026, 6, 15]);
  });
});
