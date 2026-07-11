import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

afterEach(cleanup);

/** Click the segmented-control button whose visible label is `label`. */
function clickControl(container: HTMLElement, label: string): void {
  const btn = Array.from(container.querySelectorAll<HTMLButtonElement>(".demo-seg")).find(
    (b) => b.textContent?.trim() === label,
  );
  if (!btn) throw new Error(`control not found: ${label}`);
  fireEvent.click(btn);
}

/** Click any button (outside the segmented controls) by its visible label. */
function clickButton(container: HTMLElement, label: string): void {
  const btn = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === label,
  );
  if (!btn) throw new Error(`button not found: ${label}`);
  fireEvent.click(btn);
}

/** The `data-date` of the month cell currently containing the event chip `eid`. */
function cellDateOf(container: HTMLElement, eid: string): string | undefined {
  return (
    container
      .querySelector(`[data-event-id="${eid}"]`)
      ?.closest("[data-date]")
      ?.getAttribute("data-date") ?? undefined
  );
}

/**
 * Drag the given month-view event chip onto the cell `steps` cells later, driving the real HTML5
 * drag path. dragStart is committed in its own act() so the drop handler sees the grabbed state.
 */
function dragEventByCells(
  container: HTMLElement,
  eid: string,
  steps: number,
): { originDate: string; targetDate: string } {
  const chip = container.querySelector(`[data-event-id="${eid}"][draggable="true"]`);
  if (!chip) throw new Error(`draggable chip not found: ${eid}`);
  const originDate = chip.closest("[data-date]")!.getAttribute("data-date")!;
  const cells = Array.from(container.querySelectorAll(".aethercal-day[data-date]"));
  const target = cells[cells.findIndex((c) => c.getAttribute("data-date") === originDate) + steps]!;
  const targetDate = target.getAttribute("data-date")!;
  const dataTransfer = new DataTransfer();
  act(() => {
    fireEvent.dragStart(chip, { dataTransfer });
  });
  act(() => {
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    fireEvent.dragEnd(chip, { dataTransfer });
  });
  return { originDate, targetDate };
}

describe("App (demo shell)", () => {
  it("mounts the calendar in the month view with the brand chrome", () => {
    const { container, getByText } = render(<App />);
    expect(getByText("AetherCal")).toBeTruthy();
    expect(container.querySelector('.aethercal-calendar[data-view="month"]')).toBeTruthy();
  });

  it("switches between all four views", () => {
    const { container } = render(<App />);
    clickControl(container, "Semana");
    expect(container.querySelector(".aethercal-timegrid")).toBeTruthy();
    clickControl(container, "Día");
    expect(container.querySelector('.aethercal-timegrid[data-view="day"]')).toBeTruthy();
    clickControl(container, "Agenda");
    expect(container.querySelector('.aethercal-calendar[data-view="list"]')).toBeTruthy();
    clickControl(container, "Mes");
    expect(container.querySelector('.aethercal-calendar[data-view="month"]')).toBeTruthy();
  });

  it("navigates the visible period with the built-in prev/today/next toolbar", () => {
    const { container, getByRole } = render(<App />);
    const titleOf = (): string =>
      container.querySelector(".aethercal-nav-title")?.textContent ?? "";
    const start = titleOf();
    // Next moves forward a month (the period title changes).
    fireEvent.click(getByRole("button", { name: /siguiente/i }));
    const afterNext = titleOf();
    expect(afterNext).not.toBe(start);
    // The new period still has sample events (data is rebuilt relative to the anchor).
    expect(container.querySelectorAll("[data-event-id]").length).toBeGreaterThan(0);
    // Today returns to the starting period.
    fireEvent.click(getByRole("button", { name: /^hoy$/i }));
    expect(titleOf()).toBe(start);
  });

  it("applies the theme presets to the whole page", () => {
    const { container } = render(<App />);
    const root = container.querySelector(".demo-root") as HTMLElement;
    clickControl(container, "Oscuro");
    expect(root.classList.contains("demo-mode-dark")).toBe(true);
    // The dark preset's background token is applied inline on the shell root.
    expect(root.style.getPropertyValue("--ac-bg")).toBe("#14161a");
    clickControl(container, "Claro");
    expect(root.classList.contains("demo-mode-light")).toBe(true);
  });

  it("localizes the chrome and the calendar to EN", () => {
    const { container, getByText, queryByText } = render(<App />);
    expect(queryByText("Mes")).toBeTruthy();
    clickControl(container, "EN");
    expect(queryByText("Month")).toBeTruthy();
    expect(getByText(/Open-source calendar/i)).toBeTruthy();
  });

  it("resets the sample data without crashing and keeps a usable board", () => {
    const { container } = render(<App />);
    const reset = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent?.trim() === "Restablecer datos",
    );
    expect(reset).toBeTruthy();
    fireEvent.click(reset!);
    expect(container.querySelector('.aethercal-calendar[data-view="month"]')).toBeTruthy();
    expect(container.querySelectorAll("[data-event-id]").length).toBeGreaterThan(0);
  });

  describe("optimistic reconciliation (mock server)", () => {
    const EID = "m-kickoff"; // a mid-month, editable timed event that is always in the month grid

    it("commits an accepted drag with the server's new position", async () => {
      vi.useFakeTimers();
      try {
        const { container } = render(<App />);
        const { targetDate } = dragEventByCells(container, EID, 2);
        // Optimistic: the chip is at the target and flagged pending before the server answers.
        expect(cellDateOf(container, EID)).toBe(targetDate);
        expect(container.querySelector(`[data-event-id="${EID}"]`)?.classList.contains("is-pending")).toBe(true);
        // After the 650ms mock round-trip: committed at the target, no longer pending.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(700);
        });
        expect(cellDateOf(container, EID)).toBe(targetDate);
        expect(container.querySelector(`[data-event-id="${EID}"]`)?.classList.contains("is-pending")).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });

    it("rolls back a rejected drag to the original position", async () => {
      vi.useFakeTimers();
      try {
        const { container } = render(<App />);
        clickControl(container, "Rechazar");
        const { originDate, targetDate } = dragEventByCells(container, EID, 2);
        expect(cellDateOf(container, EID)).toBe(targetDate); // optimistic move
        await act(async () => {
          await vi.advanceTimersByTimeAsync(700); // server rejects -> rollback
        });
        expect(cellDateOf(container, EID)).toBe(originDate);
        expect(container.querySelector(`[data-event-id="${EID}"]`)?.classList.contains("is-rolledback")).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });

    it("drops a mutation that is still in flight when the board is reset", async () => {
      vi.useFakeTimers();
      try {
        const { container } = render(<App />);
        const { originDate, targetDate } = dragEventByCells(container, EID, 2);
        expect(cellDateOf(container, EID)).toBe(targetDate); // pending at target
        // Reset WHILE the mutation is pending: the stale commit must NOT land on the fresh board.
        act(() => {
          clickButton(container, "Restablecer datos");
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(700);
        });
        expect(cellDateOf(container, EID)).toBe(originDate);
        expect(cellDateOf(container, EID)).not.toBe(targetDate);
      } finally {
        vi.useRealTimers();
      }
    });
  });
});
