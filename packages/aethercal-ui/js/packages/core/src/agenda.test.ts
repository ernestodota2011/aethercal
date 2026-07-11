import { describe, expect, it } from "vitest";
import { buildAgenda } from "./agenda";
import { formatLocalDateTime } from "./dateMath";
import type { CalendarEvent } from "./types";

/** ISO for local midnight `days` calendar days after 2026-01-01 (component-based, DST-safe). */
const midnightPlusDays = (days: number): string => formatLocalDateTime(new Date(2026, 0, 1 + days));

function evt(partial: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start" | "end">): CalendarEvent {
  return { title: partial.title ?? partial.id, ...partial };
}

describe("buildAgenda — grouping", () => {
  it("returns an empty agenda for no events", () => {
    expect(buildAgenda([])).toEqual([]);
  });

  it("places a single timed event under its local day, not a continuation", () => {
    const agenda = buildAgenda([
      evt({ id: "a", title: "A", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00" }),
    ]);
    expect(agenda).toHaveLength(1);
    expect(agenda[0]!.date).toBe("2026-07-15");
    expect(agenda[0]!.entries).toHaveLength(1);
    expect(agenda[0]!.entries[0]!.isContinuation).toBe(false);
    expect(agenda[0]!.entries[0]!.continuesAfter).toBe(false);
    expect(agenda[0]!.entries[0]!.event.id).toBe("a");
  });

  it("orders days chronologically regardless of input order", () => {
    const agenda = buildAgenda([
      evt({ id: "late", start: "2026-07-20T09:00:00", end: "2026-07-20T10:00:00" }),
      evt({ id: "early", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "mid", start: "2026-07-18T09:00:00", end: "2026-07-18T10:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15", "2026-07-18", "2026-07-20"]);
  });

  it("orders events within a day by start time (all-day at midnight sorts first)", () => {
    const agenda = buildAgenda([
      evt({ id: "afternoon", start: "2026-07-15T14:00:00", end: "2026-07-15T15:00:00" }),
      evt({ id: "morning", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "allday", allDay: true, start: "2026-07-15T00:00:00", end: "2026-07-16T00:00:00" }),
    ]);
    expect(agenda[0]!.entries.map((e) => e.event.id)).toEqual(["allday", "morning", "afternoon"]);
  });

  it("keeps input order for events that share the same start (stable sort)", () => {
    const agenda = buildAgenda([
      evt({ id: "first", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "second", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
    ]);
    expect(agenda[0]!.entries.map((e) => e.event.id)).toEqual(["first", "second"]);
  });
});

describe("buildAgenda — multi-day and all-day spans", () => {
  it("spans a timed event across every local day it occupies with correct edge flags", () => {
    const agenda = buildAgenda([
      evt({ id: "trip", start: "2026-07-15T22:00:00", end: "2026-07-17T02:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15", "2026-07-16", "2026-07-17"]);

    const flagsFor = (date: string) => {
      const entry = agenda.find((d) => d.date === date)!.entries[0]!;
      return [entry.isContinuation, entry.continuesAfter];
    };
    expect(flagsFor("2026-07-15")).toEqual([false, true]); // start day
    expect(flagsFor("2026-07-16")).toEqual([true, true]); // middle day
    expect(flagsFor("2026-07-17")).toEqual([true, false]); // end day
  });

  it("treats an exclusive midnight end as not occupying the end day (single all-day)", () => {
    const agenda = buildAgenda([
      evt({ id: "d", allDay: true, start: "2026-07-15T00:00:00", end: "2026-07-16T00:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15"]);
    expect(agenda[0]!.entries[0]!.continuesAfter).toBe(false);
  });

  it("spans a multi-day all-day event over its inclusive days (exclusive end excluded)", () => {
    const agenda = buildAgenda([
      evt({ id: "conf", allDay: true, start: "2026-07-15T00:00:00", end: "2026-07-18T00:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15", "2026-07-16", "2026-07-17"]);
  });

  it("places a zero-duration event on its single start day", () => {
    const agenda = buildAgenda([
      evt({ id: "z", start: "2026-07-15T10:00:00", end: "2026-07-15T10:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15"]);
  });

  it("degrades a reversed range (end before start) to the start day only", () => {
    const agenda = buildAgenda([
      evt({ id: "bad", start: "2026-07-15T10:00:00", end: "2026-07-15T09:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-07-15"]);
    expect(agenda[0]!.entries[0]!.continuesAfter).toBe(false);
  });

  it("merges different events that fall on the same day into one group, each sorted", () => {
    const agenda = buildAgenda([
      evt({ id: "trip", start: "2026-07-15T22:00:00", end: "2026-07-16T02:00:00" }),
      evt({ id: "same-day", start: "2026-07-16T09:00:00", end: "2026-07-16T10:00:00" }),
    ]);
    const day16 = agenda.find((d) => d.date === "2026-07-16")!;
    // The continuation of "trip" (started 07-15) sorts before the fresh 09:00 event.
    expect(day16.entries.map((e) => e.event.id)).toEqual(["trip", "same-day"]);
    expect(day16.entries[0]!.isContinuation).toBe(true);
    expect(day16.entries[1]!.isContinuation).toBe(false);
  });
});

describe("buildAgenda — defensive span cap (MAX_EVENT_SPAN_DAYS = 370)", () => {
  it("enumerates every day of an event spanning exactly the cap (370 days), fully shown", () => {
    // end is exclusive at day 370's midnight -> occupies days 0..369 = 370 local days.
    const agenda = buildAgenda([
      { id: "cap", title: "Long", start: "2026-01-01T00:00:00", end: midnightPlusDays(370) },
    ]);
    expect(agenda).toHaveLength(370);
    expect(agenda[agenda.length - 1]!.entries[0]!.continuesAfter).toBe(false); // fully shown, no truncation
  });

  it("caps enumeration at 370 days for a longer event, flagging the tail as continuing", () => {
    // Occupies 372 local days (0..371); the cap must show at most 370 and never more.
    const agenda = buildAgenda([
      { id: "toolong", title: "Too long", start: "2026-01-01T00:00:00", end: midnightPlusDays(372) },
    ]);
    expect(agenda).toHaveLength(370);
    expect(agenda[agenda.length - 1]!.entries[0]!.continuesAfter).toBe(true); // real tail is beyond the cap
  });
});

// A DST-observing timezone (vitest.config.ts pins TZ=America/New_York). 2026-03-08 is the
// spring-forward day (02:00 -> 03:00). Feature-detect so the suite self-skips on a runtime that
// ignores TZ instead of failing, exactly like dateMath.dst.test.ts.
const springForwardHonored = new Date(2026, 2, 8, 2, 30, 0).getHours() === 3;

describe.skipIf(!springForwardHonored)("buildAgenda — across a DST spring-forward", () => {
  it("enumerates local calendar days across the transition (no lost or gained day)", () => {
    const agenda = buildAgenda([
      evt({ id: "e", start: "2026-03-07T23:00:00", end: "2026-03-09T09:00:00" }),
    ]);
    expect(agenda.map((d) => d.date)).toEqual(["2026-03-07", "2026-03-08", "2026-03-09"]);
    expect(agenda[0]!.entries[0]!.isContinuation).toBe(false);
    expect(agenda[2]!.entries[0]!.continuesAfter).toBe(false);
  });
});
