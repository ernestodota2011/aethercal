/**
 * Keyboard navigation + keyboard drag (move AND resize) for the week/day time grid (F2-E, RNF-7).
 *
 * The grid is a single tabstop managing an `aria-activedescendant`: arrow keys move the active day
 * column, Enter descends into a column's events, a second Enter grabs an event so the arrows move it
 * (Up/Down = ±15 min, Left/Right = ±1 day) and "r" starts an end-edge resize — committing on Enter
 * (onEventDrop / onEventResize) or reverting on Escape. All with REAL keyboard events, no pointer.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15"; // Wednesday; Monday-first week is 2026-07-13 .. 2026-07-19.
const NOW = "2026-07-15T08:00:00";

function evt(p: Partial<CalendarEvent> & Pick<CalendarEvent, "id" | "start">): CalendarEvent {
  return { title: p.title ?? p.id, end: p.end ?? p.start, ...p };
}

function activeEl(grid: HTMLElement): HTMLElement | null {
  const id = grid.getAttribute("aria-activedescendant");
  return id ? document.getElementById(id) : null;
}

const timedEvent = evt({
  id: "e1",
  title: "Consulta",
  start: "2026-07-15T09:00:00",
  end: "2026-07-15T10:00:00",
});

describe("time grid — keyboard navigation", () => {
  it("is a single tabstop with an activedescendant seeded at today's column", () => {
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[]} />,
    );
    const grid = getByRole("grid");
    expect(grid.getAttribute("tabindex")).toBe("0");
    expect(grid.getAttribute("aria-describedby")).toBeTruthy();
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-15");
  });

  it("moves the active column left/right and clamps at the week edges", () => {
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[]} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-16");
    fireEvent.keyDown(grid, { key: "ArrowLeft" });
    fireEvent.keyDown(grid, { key: "ArrowLeft" });
    expect(activeEl(grid)?.dataset.date).toBe("2026-07-14");
  });

  it("creates a timed event when Enter is pressed on an empty active column", () => {
    const onRangeSelect = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[]}
        dayStartHour={8}
        dayEndHour={18}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.keyDown(getByRole("grid"), { key: "Enter" });
    // Default new-event slot = one hour at the window start (08:00).
    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T08:00:00",
      end: "2026-07-15T09:00:00",
      allDay: false,
    });
  });
});

describe("time grid — create guard & all-day (Crisol round-2)", () => {
  it("does NOT create on a column that already has a timed event", () => {
    const onRangeSelect = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onRangeSelect={onRangeSelect}
      />,
    );
    // The active column (today) already has a timed event -> Enter must not double-book it.
    fireEvent.keyDown(getByRole("grid"), { key: "Enter" });
    expect(onRangeSelect).not.toHaveBeenCalled();
  });

  it("moves an all-day event by DAY only (no time), committing a day-only recompute", () => {
    const onEventDrop = vi.fn();
    const allDay = evt({
      id: "hol",
      title: "Feriado",
      allDay: true,
      start: "2026-07-15T00:00:00",
      end: "2026-07-16T00:00:00",
    });
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[allDay]} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode -> first (the all-day event)
    fireEvent.keyDown(grid, { key: "Enter" }); // grab (move)
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // no-op for all-day (no time)
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // +1 day -> 2026-07-16
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "hol", start: "2026-07-16T00:00:00", end: "2026-07-17T00:00:00" }),
    );
  });
});

describe("time grid — keyboard move", () => {
  it("grabs a timed event, steps it by 15 minutes, and commits onEventDrop", () => {
    const onEventDrop = vi.fn();
    const { getByRole, container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    expect(getByRole("button", { name: /Consulta/ })).toBeTruthy(); // interactive -> real button
    fireEvent.keyDown(grid, { key: "Enter" }); // grid -> event mode (e1)
    fireEvent.keyDown(grid, { key: "Enter" }); // -> grab (move)
    expect(container.querySelector('[data-event-id="e1"]')?.className).toContain("is-grabbed");
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // +15 min -> 09:15
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventDrop).toHaveBeenCalledTimes(1);
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "e1",
        start: "2026-07-15T09:15:00",
        end: "2026-07-15T10:15:00",
      }),
    );
  });

  it("moves an event across days with Left/Right while grabbed", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // next day 2026-07-16
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-16T09:00:00", end: "2026-07-16T10:00:00" }),
    );
  });

  it("can step an event whose start is >1 day before the visible week — Crisol round-3", () => {
    const onEventDrop = vi.fn();
    // Starts 2026-07-10 (3 days before Monday 2026-07-13) and spans into the visible week, so it
    // renders as a continuation block and is grabbable; its grab origin day is off-screen.
    const spanning = evt({
      id: "trip",
      title: "Viaje",
      start: "2026-07-10T09:00:00",
      end: "2026-07-16T10:00:00",
    });
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[spanning]} onEventDrop={onEventDrop} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode (the spanning event's block)
    fireEvent.keyDown(grid, { key: "Enter" }); // grab -> origin day 2026-07-10 (off-screen)
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // 07-11
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // 07-12
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // 07-13 — successive steps are NOT trapped
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    // Start moved 3 days from 07-10 to 07-13 (proof the clamp no longer traps it at the origin).
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "trip", start: "2026-07-13T09:00:00" }),
    );
  });

  it("does NOT mutate an event whose start is before the window when confirmed without moving — Crisol round-4", () => {
    const onEventDrop = vi.fn();
    // Starts 06:00 (before an 08–18 window) but extends into it, so it renders and is grabbable.
    // A grab confirmed with no arrow press must not snap its start to the window.
    const early = evt({ id: "e1", title: "Temprano", start: "2026-07-15T06:00:00", end: "2026-07-15T10:00:00" });
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[early]}
        dayStartHour={8}
        dayEndHour={18}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "Enter" }); // confirm WITHOUT an arrow -> no-op
    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("does NOT mutate on a resize confirmed without moving when the end is after the window — Crisol round-4", () => {
    const onEventResize = vi.fn();
    // Ends 20:00, after an 08–18 window.
    const late = evt({ id: "e1", title: "Tarde", start: "2026-07-15T16:00:00", end: "2026-07-15T20:00:00" });
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[late]}
        dayStartHour={8}
        dayEndHour={18}
        onEventResize={onEventResize}
        onEventDrop={() => {}}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "r" }); // resize grab
    fireEvent.keyDown(grid, { key: "Enter" }); // confirm WITHOUT an arrow -> no-op
    expect(onEventResize).not.toHaveBeenCalled();
  });

  it("preserves the original out-of-window time on a day-only keyboard move — Crisol round-4", () => {
    const onEventDrop = vi.fn();
    const early = evt({ id: "e1", title: "Temprano", start: "2026-07-15T06:00:00", end: "2026-07-15T10:00:00" });
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[early]}
        dayStartHour={8}
        dayEndHour={18}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "ArrowRight" }); // move a DAY, not the time
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    // The 06:00 start time is preserved (not snapped to the 08:00 window start).
    expect(onEventDrop).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-16T06:00:00", end: "2026-07-16T10:00:00" }),
    );
  });

  it("reverts a grabbed move on Escape", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "Escape" });
    expect(onEventDrop).not.toHaveBeenCalled();
  });
});

describe("time grid — focus robustness (Crisol round-5)", () => {
  it("a step blocked by the window clamp is NOT a move (confirm stays a no-op)", () => {
    const onEventDrop = vi.fn();
    // Starts at the window top (08:00). ArrowUp is clamped at the first slot -> no real movement.
    const atTop = evt({ id: "e1", title: "Top", start: "2026-07-15T08:00:00", end: "2026-07-15T09:00:00" });
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[atTop]}
        dayStartHour={8}
        dayEndHour={18}
        onEventDrop={onEventDrop}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "Enter" }); // grab
    fireEvent.keyDown(grid, { key: "ArrowUp" }); // clamped at the top -> no move
    fireEvent.keyDown(grid, { key: "Enter" }); // confirm
    expect(onEventDrop).not.toHaveBeenCalled();
  });

  it("falls back to the column when the active event is removed by the parent", () => {
    const { getByRole, rerender } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[timedEvent]} onEventDrop={() => {}} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode -> e1 active
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("e1");
    rerender(<AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[]} onEventDrop={() => {}} />);
    const active = activeEl(grid);
    expect(active).not.toBeNull(); // no dangling aria-activedescendant
    expect(active?.getAttribute("data-event-id")).toBeNull(); // it is a day column now
  });
});

describe("time grid — mutation callbacks fire exactly once (Crisol round-6)", () => {
  it("calls onEventDrop exactly once per keyboard move, even under StrictMode", () => {
    const onEventDrop = vi.fn();
    const { getByRole } = render(
      <StrictMode>
        <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[timedEvent]} onEventDrop={onEventDrop} />
      </StrictMode>,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    fireEvent.keyDown(grid, { key: "Enter" });
    // The mutation callback must not double-fire (it runs in the event handler body, not a state
    // updater that StrictMode/concurrent React may re-invoke).
    expect(onEventDrop).toHaveBeenCalledTimes(1);
  });
});

describe("time grid — actionable-only navigation & valid create (Crisol round-7)", () => {
  it("does not enter or expose a locked event as actionable", () => {
    const locked = evt({
      id: "e1",
      title: "Bloqueado",
      editable: false,
      start: "2026-07-15T09:00:00",
      end: "2026-07-15T10:00:00",
    });
    const { getByRole, queryByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[locked]} onEventDrop={() => {}} />,
    );
    const grid = getByRole("grid");
    expect(queryByRole("button")).toBeNull(); // a locked event is not a button
    fireEvent.keyDown(grid, { key: "Enter" }); // no navigable event, column not empty -> no-op
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBeNull(); // still on the column
  });

  it("skips a locked event and lands on the first actionable one", () => {
    const events = [
      evt({ id: "locked", title: "Bloqueado", editable: false, start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }),
      evt({ id: "open", title: "Abierto", start: "2026-07-15T11:00:00", end: "2026-07-15T12:00:00" }),
    ];
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={events} onEventDrop={() => {}} />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" });
    expect(activeEl(grid)?.getAttribute("data-event-id")).toBe("open");
  });

  it("creates a POSITIVE-duration range in a 1-hour window (never zero-length)", () => {
    const onRangeSelect = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[]}
        dayStartHour={8}
        dayEndHour={9}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.keyDown(getByRole("grid"), { key: "Enter" });
    expect(onRangeSelect).toHaveBeenCalledWith({
      start: "2026-07-15T08:00:00",
      end: "2026-07-15T09:00:00",
      allDay: false,
    });
  });
});

describe("time grid — keyboard resize", () => {
  it("resizes the end edge with 'r' + arrows and commits onEventResize", () => {
    const onEventResize = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onEventResize={onEventResize}
        onEventDrop={() => {}}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "r" }); // start end-edge resize (end = 10:00)
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // +15 min -> 10:15
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventResize).toHaveBeenCalledTimes(1);
    expect(onEventResize).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-15T09:00:00", end: "2026-07-15T10:15:00" }),
    );
  });

  it("is reachable when ONLY onEventResize is wired (no drop/click) — Crisol round-1", () => {
    const onEventResize = vi.fn();
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[timedEvent]} onEventResize={onEventResize} />,
    );
    const grid = getByRole("grid");
    // A resize-only, editable event is still keyboard-actionable (a real button).
    expect(getByRole("button", { name: /Consulta/ })).toBeTruthy();
    fireEvent.keyDown(grid, { key: "Enter" }); // grid -> event mode (works with resize-only now)
    fireEvent.keyDown(grid, { key: "r" }); // start end-edge resize
    fireEvent.keyDown(grid, { key: "ArrowDown" }); // +15 min
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventResize).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", end: "2026-07-15T10:15:00" }),
    );
  });

  it("shortens an event ending exactly at midnight to 23:45 of the previous day — Crisol round-8", () => {
    const onEventResize = vi.fn();
    // Ends at 00:00 of 2026-07-16 (its resize grab origin is that midnight boundary).
    const untilMidnight = evt({
      id: "e1",
      title: "Nocturno",
      start: "2026-07-15T22:00:00",
      end: "2026-07-16T00:00:00",
    });
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[untilMidnight]}
        onEventResize={onEventResize}
        onEventDrop={() => {}}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" }); // event mode
    fireEvent.keyDown(grid, { key: "r" }); // resize the end edge (starts at 2026-07-16 00:00)
    fireEvent.keyDown(grid, { key: "ArrowUp" }); // -15 min -> rolls back to 2026-07-15 23:45
    fireEvent.keyDown(grid, { key: "Enter" }); // commit
    expect(onEventResize).toHaveBeenCalledWith(
      expect.objectContaining({ id: "e1", start: "2026-07-15T22:00:00", end: "2026-07-15T23:45:00" }),
    );
  });

  it("announces the resize through the polite live region", () => {
    const { getByRole, container } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[timedEvent]}
        onEventResize={() => {}}
        onEventDrop={() => {}}
      />,
    );
    const grid = getByRole("grid");
    fireEvent.keyDown(grid, { key: "Enter" });
    fireEvent.keyDown(grid, { key: "r" });
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain("Consulta");
  });
});
