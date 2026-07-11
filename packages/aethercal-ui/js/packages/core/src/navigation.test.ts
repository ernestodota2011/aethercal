/**
 * Headless period-navigation geometry (F2-NAV).
 *
 * `getVisibleRange` answers "which period is on screen" (the payload for on_range_change /
 * on_view_change) and `stepAnchor` moves the anchor one period at a time (prev/next). Both are pure,
 * dependency-free, and DST-safe by construction (component-based date math), like the rest of the
 * core — the "what period" geometry lives here, never in the React layer or a consumer (RF-23).
 */
import { describe, expect, it } from "vitest";
import { parseLocalDateTime } from "./dateMath";
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

  it("re-anchoring on the emitted `from` reproduces the same range (controlled round-trip)", () => {
    // The controlled contract: the consumer sets anchor = payload.from; recomputing must be a no-op.
    for (const view of ["month", "week", "day", "list"] as const) {
      const stepped = stepAnchor(new Date(2026, 6, 15), view, 1);
      const range = getVisibleRange(view, stepped, 1);
      const reanchored = getVisibleRange(view, parseLocalDateTime(range.from), 1);
      expect(reanchored).toEqual(range);
    }
  });
});
