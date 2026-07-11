/**
 * Keyboard navigation + keyboard drag for the month view (F2-E a11y, RNF-7).
 *
 * The grid is a single tabstop that manages an `aria-activedescendant`: arrow keys move the active
 * day cell, Enter on an empty cell creates, Enter on an event enters "event mode", and a second
 * Enter grabs the event so the arrow keys move it across days — with a live-region announcement —
 * committing on Enter (onEventDrop) or reverting on Escape. All exercised with REAL keyboard events.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15"; // Wednesday; index 16 in the Monday-first July 2026 grid.

function evt(p: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start">): CalendarEvent {
  return { title: p.title ?? p.id, end: p.end ?? p.start, ...p };
}

/** The element the grid's aria-activedescendant currently points at. */
function activeEl(grid: HTMLElement): HTMLElement | null {
  const id = grid.getAttribute("aria-activedescendant");
  return id ? document.getElementById(id) : null;
}

describe("month view — keyboard navigation", () => {
  it("is a single tabstop with an aria-activedescendant seeded at the anchor day", () => {
    const { getByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    const grid = getByRole("grid");
    expect(grid.getAttribute("tabindex")).toBe("0");
    expect(grid.getAttribute("aria-describedby")).toBeTruthy(); // the keyboard usage hint
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-15");
  });

  it("moves the active cell with the arrow keys", () => {
    const { getByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-16");
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-23");
    fireEvent.keyDown(grid, { key: "ArrowLeft" });
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-22");
  });

  it("creates on the active empty cell when Enter is pressed", () => {
    const onRangeSelect = vi.fn();
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} onRangeSelect={onRangeSelect} />,
    );
    fireEvent.keyDown(getByRole("grid"), { key: "Enter" });
    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T00:00:00",
      end: "2026-07-16T00:00:00",
      allDay: true,
    });
  });
});

describe("month view — keyboard drag (grab / move / drop)", () => {
  const events = [
    evt({ id: "e1", title: "Consulta", start: "2026-07-15T14:00:00", end: "2026-07-15T15:00:00" }),
  ];

  it("exposes an interactive event as a button and enters event mode on Enter", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    // A wired, editable event is a real button now (keyboard action exists) — not an ARIA lie.
    expect(getByRole("button", { name: /Consulta/ })).toBeTruthy();
    fireEvent.keyDown(grid, { key: "Enter" }); // active cell has an event -> event mode
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("e1");
  });

  it("grabs, moves across days, and commits onEventDrop with the recomputed range", () => {
    const onEventDrop = vi.fn();
    const { getByRole, container } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // -> event mode (e1)
    fireEvent.keyDown(grid, { key: "Enter" }); // -> grab
    expect(container.querySelector('[data-event-id="e1"]')?.className).toContain("is-grabbed");
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // move target to 2026-07-16
    expect(container.querySelector('[data-date="2026-07-16"]')?.className).toContain(
      "is-drop-target",
    );
    fireEvent.keyDown(grid, { key: "Enter" }); // drop
    expect(onEventDrop).toHaveBeenCalledTimes(1);
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "e1",
        start: "2026-07-16T14:00:00",
        end: "2026-07-16T15:00:00",
      }),
    );
  });

  it("cancels the grab on Escape without committing", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // move target
    fireEvent.keyDown(grid, { key: "Escape" }); // cancel
    fireEvent.keyDown(grid, { key: "Enter" }); // would re-grab, not drop
    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("announces the grab through a polite live region", () => {
    const onEventDrop = vi.fn();
    const { getByRole, container } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain("Consulta");
  });

  it("reaches an event hidden behind '+N more' by auto-expanding the day — Crisol round-1", () => {
    const onEventDrop = vi.fn();
    const many = Array.from({ length: 5 }, (_, i) =>
      evt({
        id: `e${i}`,
        title: `Event ${i}`,
        start: `2026-07-15T0${i + 1}:00:00`,
        end: `2026-07-15T0${i + 1}:30:00`,
      }),
    );
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={many}
        maxEventsPerDay={3}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode + auto-expand -> all 5 rendered
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // -> e4, which was hidden behind "+2 more"
    // The active descendant resolves to a real, rendered node (no dangling id).
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("e4");
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // to 2026-07-16
    fireEvent.keyDown(grid, { key: "Enter" }); // drop
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e4", start: "2026-07-16T05:00:00" }),
    );
  });

  it("falls back to the cell when the active event is removed by the parent — Crisol round-5", () => {
    const { getByRole, rerender } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={() => {}} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode -> active is e1
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("e1");
    // Parent removes the event (e.g. it was cancelled) while it is the active descendant.
    rerender(<AetherCalendar view="month" anchor={ANCHOR} events={[]} onEventDrop={() => {}} />);
    const active = activeEl(grid);
    expect(active).not.toBeNull(); // no dangling aria-activedescendant
    expect(active?.getAttribute("data-event-id")).toBeNull(); // it is a day cell now
    expect(active?.dataset.date).toBe("2026-07-15");
  });

  it("does NOT create on a day that already has events — Crisol round-2", () => {
    const onRangeSelect = vi.fn();
    // Events present but NO onEventDrop/onEventClick -> non-interactive. Enter must be a no-op, not
    // a silent double-booking of an occupied day.
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={events}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.keyDown(getByRole("grid"), { key: "Enter" });
    expect(onRangeSelect).not.toHaveBeenCalled();
  });

  it("skips a locked event and lands on the first actionable one — Crisol round-7", () => {
    const mixed = [
      evt({ id: "locked", title: "Bloqueado", editable: false, start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "open", title: "Abierto", start: "2026-07-15T11:00:00", end: "2026-07-15T12:00:00" }),
    ];
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={mixed} onEventDrop={() => {}} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode -> first ACTIONABLE event
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("open");
  });

  it("does not turn a non-interactive (unwired) event into a button", () => {
    const { queryByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} />,
    );
    // No onEventDrop/onEventClick wired -> no keyboard action -> no button (honest a11y).
    expect(queryByRole("button")).toBeNull();
  });
});
