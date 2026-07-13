/**
 * Cross-view ARIA-role coherence + no-ARIA-lies (F2-E, RNF-7, AetherCal-06 §11 RF-20).
 *
 * The month/week/day surfaces use a real `grid` pattern (grid → row → columnheader/gridcell); the
 * list surface uses real list semantics (group → list → listitem) and is NOT a grid. Every grid is a
 * single tabstop with a keyboard-usage description, and no view exposes a stray `status` role or a
 * dishonest `button` (an unwired event is not a button). This is the RF-20 "renders in all four
 * views" a11y check made deterministic.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15";
const NOW = "2026-07-15T09:00:00";
const events: CalendarEvent[] = [
  { id: "e1", title: "Consulta", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" },
];

describe("month view roles", () => {
  it("is a keyboard-navigable ARIA grid with 7 columnheaders and 42 gridcells", () => {
    const { getByRole, getAllByRole, queryByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} />,
    );
    const grid = getByRole("grid");
    expect(grid.getAttribute("tabindex")).toBe("0");
    expect(grid.getAttribute("aria-describedby")).toBeTruthy();
    expect(getAllByRole("columnheader")).toHaveLength(7);
    expect(getAllByRole("gridcell")).toHaveLength(42);
    expect(getAllByRole("row").length).toBeGreaterThanOrEqual(7); // weekday row + 6 week rows
    expect(queryByRole("status")).toBeNull(); // the live region is aria-live, not role=status
  });
});

describe("week / day view roles", () => {
  it("week is an ARIA grid with 7 day columnheaders", () => {
    const { getByRole, getAllByRole, queryByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={events} />,
    );
    expect(getByRole("grid").getAttribute("tabindex")).toBe("0");
    expect(getAllByRole("columnheader")).toHaveLength(7);
    expect(getAllByRole("gridcell").length).toBeGreaterThan(0);
    expect(queryByRole("status")).toBeNull();
  });

  it("day is an ARIA grid with a single day columnheader", () => {
    const { getByRole, getAllByRole } = render(
      <AetherCalendar view="day" anchor={ANCHOR} now={NOW} events={events} />,
    );
    expect(getByRole("grid")).toBeTruthy();
    expect(getAllByRole("columnheader")).toHaveLength(1);
  });

  it("makes the scrollable hour body keyboard-focusable (scrollable-region-focusable)", () => {
    // The hour body (overflow-y:auto, fixed height) must be reachable by keyboard so a keyboard-only
    // user can scroll it; without tabindex axe flags it in week and day.
    for (const view of ["week", "day"] as const) {
      const { container, unmount } = render(
        <AetherCalendar view={view} anchor={ANCHOR} now={NOW} events={events} />,
      );
      const body = container.querySelector(".aethercal-tg-body") as HTMLElement;
      expect(body, `${view}: missing .aethercal-tg-body`).toBeTruthy();
      expect(body.getAttribute("tabindex"), `${view}: body not focusable`).toBe("0");
      unmount();
    }
  });
});

describe("list view roles (list semantics, NOT a grid)", () => {
  it("uses group/list/listitem and never a grid or status role", () => {
    const { queryByRole, getAllByRole } = render(
      <AetherCalendar view="list" anchor={ANCHOR} events={events} />,
    );
    expect(queryByRole("grid")).toBeNull();
    expect(queryByRole("status")).toBeNull();
    expect(getAllByRole("group")).toHaveLength(1);
    expect(getAllByRole("list").length).toBeGreaterThanOrEqual(1);
    expect(getAllByRole("listitem").length).toBeGreaterThanOrEqual(1);
  });
});

describe("no ARIA lies", () => {
  it("does not expose an unwired event as a button in any view", () => {
    for (const view of ["month", "week", "day", "list"] as const) {
      const { queryByRole, unmount } = render(
        <AetherCalendar view={view} anchor={ANCHOR} now={NOW} events={events} />,
      );
      expect(queryByRole("button")).toBeNull();
      unmount();
    }
  });

  it("exposes a wired, editable event as a button on the grid views", () => {
    const { getAllByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={() => {}} />,
    );
    expect(getAllByRole("button", { name: /Consulta/ }).length).toBeGreaterThanOrEqual(1);
  });
});

describe("no dishonest drag affordance", () => {
  const RESOURCES = [{ id: "h1", title: "Dr. Rivas" }];
  const homed = events.map((e) => ({ ...e, resourceId: "h1" }));

  it("marks NO event draggable in any view when the host wired no onEventDrop", () => {
    // A draggable chip with nowhere to drop is a silent no-op: the user drags it, releases it, and
    // nothing happens — no error, no change. The affordance must not exist unless the drop can land.
    for (const view of ["month", "week", "day", "timeline"] as const) {
      const { container, unmount } = render(
        <AetherCalendar
          view={view}
          anchor={ANCHOR}
          now={NOW}
          events={homed}
          resources={RESOURCES}
        />,
      );
      expect(container.querySelector('[draggable="true"]'), `${view}: dishonest drag`).toBeNull();
      unmount();
    }
  });

  it("marks an editable event draggable in every view once onEventDrop is wired", () => {
    for (const view of ["month", "week", "day", "timeline"] as const) {
      const { container, unmount } = render(
        <AetherCalendar
          view={view}
          anchor={ANCHOR}
          now={NOW}
          events={homed}
          resources={RESOURCES}
          onEventDrop={() => {}}
        />,
      );
      expect(container.querySelector('[draggable="true"]'), `${view}: missing drag`).toBeTruthy();
      unmount();
    }
  });
});
