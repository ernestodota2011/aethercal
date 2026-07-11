import { describe, expect, it } from "vitest";
import { buildSampleEvents, toLocalIso } from "./sampleData";

const ISO_LOCAL = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/;

/** Local-day part ("YYYY-MM-DD") of a naive-local ISO string. */
function dayOf(iso: string): string {
  return iso.slice(0, 10);
}

describe("toLocalIso", () => {
  it("emits naive local wall-time with no offset and zero-padded fields", () => {
    // Month is 0-based in the Date constructor: this is 2026-03-05T07:09:00 local.
    expect(toLocalIso(new Date(2026, 2, 5, 7, 9))).toBe("2026-03-05T07:09:00");
  });

  it("never appends a UTC 'Z' or timezone offset", () => {
    const iso = toLocalIso(new Date(2026, 11, 31, 23, 45));
    expect(iso.endsWith("Z")).toBe(false);
    expect(iso).toMatch(ISO_LOCAL);
  });
});

describe("buildSampleEvents", () => {
  // A mid-month Wednesday keeps the fixtures away from month-boundary edge effects.
  const today = new Date(2026, 6, 15, 12, 0); // 2026-07-15
  const events = buildSampleEvents(today);

  it("produces a rich, non-trivial set with unique ids", () => {
    expect(events.length).toBeGreaterThanOrEqual(15);
    const ids = events.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every event has valid naive-local ISO start/end with end after start", () => {
    for (const e of events) {
      expect(e.start).toMatch(ISO_LOCAL);
      expect(e.end).toMatch(ISO_LOCAL);
      expect(e.end > e.start).toBe(true);
    }
  });

  it("includes at least one all-day event and at least one multi-day all-day band", () => {
    const allDay = events.filter((e) => e.allDay);
    expect(allDay.length).toBeGreaterThanOrEqual(2);
    const multiDay = allDay.filter((e) => dayOf(e.end) > dayOf(e.start));
    expect(multiDay.length).toBeGreaterThanOrEqual(1);
  });

  it("includes a cross-midnight timed event (spans two calendar days, not all-day)", () => {
    const crossMidnight = events.filter(
      (e) => !e.allDay && dayOf(e.end) > dayOf(e.start),
    );
    expect(crossMidnight.length).toBeGreaterThanOrEqual(1);
    expect(crossMidnight.some((e) => e.id === "release")).toBe(true);
  });

  it("includes overlapping timed events on the same day (exercises the lane layout)", () => {
    const standup = events.find((e) => e.id === "standup");
    const design = events.find((e) => e.id === "design");
    expect(standup && design).toBeTruthy();
    // Overlap: design starts before standup ends and ends after standup starts.
    expect(design!.start < standup!.end).toBe(true);
    expect(design!.end > standup!.start).toBe(true);
  });

  it("piles more than the default 3 events on one day (drives month '+N more')", () => {
    const perDay = new Map<string, number>();
    for (const e of events) {
      if (e.allDay) continue;
      const d = dayOf(e.start);
      perDay.set(d, (perDay.get(d) ?? 0) + 1);
    }
    const busiest = Math.max(...perDay.values());
    expect(busiest).toBeGreaterThan(3);
  });

  it("anchors the this-week cluster to the supplied today", () => {
    const standup = events.find((e) => e.id === "standup");
    expect(dayOf(standup!.start)).toBe("2026-07-15");
  });

  it("marks some events non-editable and gives some a color accent", () => {
    expect(events.some((e) => e.editable === false)).toBe(true);
    expect(events.some((e) => typeof e.color === "string")).toBe(true);
  });

  it("is deterministic for a given today", () => {
    expect(buildSampleEvents(new Date(2026, 6, 15, 12, 0))).toEqual(events);
  });
});
