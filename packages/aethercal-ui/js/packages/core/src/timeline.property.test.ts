import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { formatLocalDateTime, parseLocalDateTime } from "./dateMath";
import { resolveTimeGridConfig } from "./timeGrid";
import { buildResourceTimeline } from "./timeline";
import type { CalendarEvent, CalendarResource } from "./types";

const DAYS = ["2026-07-13", "2026-07-14", "2026-07-15"];
const ORIGIN = parseLocalDateTime(`${DAYS[0]}T00:00:00`).getTime();
const AXIS_END_MIN = DAYS.length * 24 * 60;
const FULL_DAY = resolveTimeGridConfig();

const RESOURCES: CalendarResource[] = [
  { id: "h1", title: "H1", groupId: "Clinic A" },
  { id: "h2", title: "H2", groupId: "Clinic A" },
  { id: "h3", title: "H3", groupId: "Clinic B" },
  { id: "h4", title: "H4" },
];
const RESOURCE_IDS = RESOURCES.map((r) => r.id);

/** Minutes from the axis origin (midnight of the first visible day) to an event's endpoint. */
function minutesOf(iso: string): number {
  return (parseLocalDateTime(iso).getTime() - ORIGIN) / 60_000;
}

function overlaps(a: CalendarEvent, b: CalendarEvent): boolean {
  return minutesOf(a.start) < minutesOf(b.end) && minutesOf(b.start) < minutesOf(a.end);
}

/** The row key a block is expected to land in: its resource id, or null for the unassigned row. */
const rowKeyOf = (row: { resource: CalendarResource | null }): string | null =>
  row.resource?.id ?? null;

/**
 * Arbitrary events across (and beyond) the 3-day window, each optionally bound to a resource — so
 * the properties also exercise out-of-window clipping and the unassigned row.
 */
const events = fc
  .array(
    fc.record({
      // -600..+5400 minutes spans a day before the window through a day after it.
      startMin: fc.integer({ min: -600, max: 5400 }),
      durMin: fc.integer({ min: 0, max: 600 }),
      // `undefined` (unassigned) and "ghost" (unknown id) are both legitimate inputs.
      resourceId: fc.constantFrom(...RESOURCE_IDS, "ghost", undefined),
      allDay: fc.boolean(),
    }),
    { maxLength: 12 },
  )
  .map((specs) =>
    specs.map((s, i): CalendarEvent => {
      const start = formatLocalDateTime(new Date(ORIGIN + s.startMin * 60_000));
      const end = formatLocalDateTime(new Date(ORIGIN + (s.startMin + s.durMin) * 60_000));
      return {
        id: `e${i}`,
        title: `e${i}`,
        start,
        end,
        ...(s.resourceId !== undefined ? { resourceId: s.resourceId } : {}),
        ...(s.allDay ? { allDay: true } : {}),
      };
    }),
  );

const rowsOf = (timeline: ReturnType<typeof buildResourceTimeline>) =>
  timeline.items.flatMap((item) => (item.kind === "row" ? [item.row] : []));

describe("buildResourceTimeline invariants", () => {
  it("never puts two overlapping events of the same resource in the same lane", () => {
    fc.assert(
      fc.property(events, (evts) => {
        for (const row of rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY))) {
          for (let i = 0; i < row.blocks.length; i += 1) {
            for (let j = i + 1; j < row.blocks.length; j += 1) {
              const a = row.blocks[i]!;
              const b = row.blocks[j]!;
              // All-day events are normalized to whole-day spans, so raw-time overlap does not
              // describe them; the lane invariant is asserted on the timed pairs.
              if (a.allDay || b.allDay) continue;
              if (a.lane === b.lane) expect(overlaps(a.event, b.event)).toBe(false);
            }
          }
        }
      }),
    );
  });

  it("keeps every lane index within its cluster's lane count, and the row's height above both", () => {
    fc.assert(
      fc.property(events, (evts) => {
        for (const row of rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY))) {
          expect(row.laneCount).toBeGreaterThanOrEqual(1);
          for (const block of row.blocks) {
            expect(block.lane).toBeGreaterThanOrEqual(0);
            expect(block.lane).toBeLessThan(block.laneCount);
            // The row's rendered height must accommodate its widest cluster.
            expect(block.laneCount).toBeLessThanOrEqual(row.laneCount);
          }
        }
      }),
    );
  });

  it("keeps every block inside the axis (0 <= left, left + width <= 1)", () => {
    fc.assert(
      fc.property(events, (evts) => {
        for (const row of rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY))) {
          for (const block of row.blocks) {
            expect(block.leftFraction).toBeGreaterThanOrEqual(0);
            expect(block.widthFraction).toBeGreaterThanOrEqual(0);
            expect(block.leftFraction + block.widthFraction).toBeLessThanOrEqual(1 + 1e-9);
          }
        }
      }),
    );
  });

  it("renders each event as AT MOST ONE bar (a continuous event never fragments per day)", () => {
    fc.assert(
      fc.property(events, (evts) => {
        const counts = new Map<string, number>();
        for (const row of rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY))) {
          for (const block of row.blocks) {
            counts.set(block.event.id, (counts.get(block.event.id) ?? 0) + 1);
          }
        }
        for (const count of counts.values()) expect(count).toBe(1);
      }),
    );
  });

  it("never loses an event: anything overlapping the window lands in exactly one row", () => {
    fc.assert(
      fc.property(events, (evts) => {
        const placed = new Set(
          rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY)).flatMap((row) =>
            row.blocks.map((b) => b.event.id),
          ),
        );
        for (const event of evts) {
          const startMin = minutesOf(event.start);
          const endMin = minutesOf(event.end);
          // An event overlapping the window must be rendered somewhere — in its resource's row, or
          // (when its resourceId is absent/unknown) in the unassigned row. Nothing vanishes.
          // An all-day event is normalized to whole days, so a zero-length one still occupies a day.
          const overlapsWindow = event.allDay
            ? startMin < AXIS_END_MIN && endMin >= 0
            : startMin < AXIS_END_MIN && endMin > 0;
          if (overlapsWindow) expect(placed.has(event.id)).toBe(true);
        }
      }),
    );
  });

  it("routes every placed event to the row its resourceId names", () => {
    fc.assert(
      fc.property(events, (evts) => {
        for (const row of rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY))) {
          for (const block of row.blocks) {
            const declared = block.event.resourceId;
            const known = declared !== undefined && RESOURCE_IDS.includes(declared);
            // A known resource id lands in that resource's row; anything else lands unassigned.
            expect(rowKeyOf(row)).toBe(known ? declared : null);
          }
        }
      }),
    );
  });

  it("collapsing a group hides exactly its own rows and leaves every other row identical", () => {
    fc.assert(
      fc.property(events, (evts) => {
        const open = rowsOf(buildResourceTimeline(RESOURCES, evts, DAYS, FULL_DAY));
        const survivors = rowsOf(
          buildResourceTimeline(RESOURCES, evts, DAYS, {
            ...FULL_DAY,
            collapsedGroupIds: ["Clinic A"],
          }),
        );
        // No row of the collapsed group survives...
        expect(survivors.every((row) => row.groupId !== "Clinic A")).toBe(true);
        // ...and every surviving row is exactly the row it was when the group was expanded (collapse
        // is a pure visibility filter — it must never re-pack or re-home anyone else's events).
        for (const row of survivors) {
          const before = open.find((r) => rowKeyOf(r) === rowKeyOf(row));
          expect(before).toBeDefined();
          expect(row.blocks).toEqual(before?.blocks);
        }
        // Every non-collapsed row is still present.
        const expected = open.filter((r) => r.groupId !== "Clinic A").map(rowKeyOf);
        expect(survivors.map(rowKeyOf)).toEqual(expected);
      }),
    );
  });
});
