/**
 * Navigation toolbar behavior (F2-NAV): the opt-in prev/today/next controls + view switcher that
 * let a consumer move the visible PERIOD, emitting the controlled `onRangeChange` / `onViewChange`.
 * The bug this closes: the calendar could only ever show the period containing "today".
 */
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import type * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15"; // a Wednesday
const NOW = "2026-07-15T09:00:00";

describe("AetherCalendar — navigation is opt-in (retrocompatible)", () => {
  it("renders no toolbar by default", () => {
    const { queryByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    expect(queryByRole("toolbar")).toBeNull();
  });

  it("renders an accessible toolbar with previous / today / next when enabled", () => {
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} navigation locale="en" />,
    );
    const toolbar = getByRole("toolbar");
    expect(within(toolbar).getByRole("button", { name: /previous/i })).toBeTruthy();
    expect(within(toolbar).getByRole("button", { name: /^today$/i })).toBeTruthy();
    expect(within(toolbar).getByRole("button", { name: /next/i })).toBeTruthy();
  });
});

describe("AetherCalendar — period navigation emits onRangeChange", () => {
  it("Next moves to the following month (month view)", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /next/i }));
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "month",
      from: "2026-08-01T00:00:00",
      to: "2026-09-01T00:00:00",
    });
  });

  it("Previous moves to the prior month", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /previous/i }));
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "month",
      from: "2026-06-01T00:00:00",
      to: "2026-07-01T00:00:00",
    });
  });

  it("Today jumps to the period containing `now`, even from a far anchor", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor="2020-01-10"
        now={NOW}
        events={[]}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /^today$/i }));
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "month",
      from: "2026-07-01T00:00:00",
      to: "2026-08-01T00:00:00",
    });
  });

  it("steps by a WEEK in week view", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="week"
        anchor={ANCHOR}
        now={NOW}
        events={[]}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /next/i }));
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "week",
      from: "2026-07-20T00:00:00",
      to: "2026-07-27T00:00:00",
    });
  });

  it("steps by the timeline's OWN window, not by a hardcoded week", () => {
    // The timeline's period IS its window, so a 3-day timeline must step by 3 days. Stepping by the
    // 7-day default would skip four whole days on every click — and would contradict the range the
    // toolbar then emits, which already honours `timelineDays`.
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="timeline"
        anchor={ANCHOR} // 2026-07-15
        now={NOW}
        events={[]}
        resources={[{ id: "h1", title: "Dr. Rivas" }]}
        timelineDays={3}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );

    fireEvent.click(getByRole("button", { name: /next/i }));
    expect(onRangeChange).toHaveBeenLastCalledWith({
      view: "timeline",
      from: "2026-07-18T00:00:00", // +3 days, not +7
      to: "2026-07-21T00:00:00",
    });

    fireEvent.click(getByRole("button", { name: /previous/i }));
    expect(onRangeChange).toHaveBeenLastCalledWith({
      view: "timeline",
      from: "2026-07-12T00:00:00", // -3 days from the anchor
      to: "2026-07-15T00:00:00",
    });
  });

  it("tiles the timeline's periods with no gap and no overlap as you step", () => {
    // `from` doubles as the next anchor, so consecutive periods must abut exactly: the `to` of one
    // period is the `from` of the next. A wrong step size shows up here as a hole or a repeat.
    const onRangeChange = vi.fn();
    const { getByRole, rerender } = render(
      <AetherCalendar
        view="timeline"
        anchor={ANCHOR}
        now={NOW}
        events={[]}
        resources={[{ id: "h1", title: "Dr. Rivas" }]}
        timelineDays={5}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );

    fireEvent.click(getByRole("button", { name: /next/i }));
    const first = onRangeChange.mock.calls[0]![0];
    expect(first).toMatchObject({ from: "2026-07-20T00:00:00", to: "2026-07-25T00:00:00" });

    // Feed the emitted anchor back in (the controlled loop) and step again.
    rerender(
      <AetherCalendar
        view="timeline"
        anchor={first.from}
        now={NOW}
        events={[]}
        resources={[{ id: "h1", title: "Dr. Rivas" }]}
        timelineDays={5}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /next/i }));
    const second = onRangeChange.mock.calls[1]![0];

    expect(second.from).toBe(first.to); // abuts exactly: no day skipped, none shown twice
    expect(second).toMatchObject({ from: "2026-07-25T00:00:00", to: "2026-07-30T00:00:00" });
  });

  it("steps by a DAY in day view", () => {
    const onRangeChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="day"
        anchor={ANCHOR}
        now={NOW}
        events={[]}
        navigation
        locale="en"
        onRangeChange={onRangeChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /next/i }));
    expect(onRangeChange).toHaveBeenCalledWith({
      view: "day",
      from: "2026-07-16T00:00:00",
      to: "2026-07-17T00:00:00",
    });
  });
});

describe("AetherCalendar — the grid actually moves when the anchor changes (controlled loop)", () => {
  it("re-renders around the new anchor a consumer feeds back from onRangeChange.from", () => {
    // Simulate the controlled loop: onRangeChange gives {from}; the host sets anchor = from.
    const { container, rerender, getByRole } = render(
      <AetherCalendar view="month" anchor="2026-07-15" events={[]} navigation locale="en" />,
    );
    // July grid does not contain an August mid-month day.
    expect(container.querySelector('[data-date="2026-08-15"]')).toBeNull();
    let nextFrom = "";
    rerender(
      <AetherCalendar
        view="month"
        anchor="2026-07-15"
        events={[]}
        navigation
        locale="en"
        onRangeChange={(p) => {
          nextFrom = p.from;
        }}
      />,
    );
    fireEvent.click(getByRole("button", { name: /next/i }));
    // Host applies the emitted `from` as the new anchor.
    rerender(<AetherCalendar view="month" anchor={nextFrom} events={[]} navigation locale="en" />);
    // The grid now contains an August day it did not before.
    expect(container.querySelector('[data-date="2026-08-15"]')).not.toBeNull();
  });
});

describe("AetherCalendar — view switcher emits onViewChange", () => {
  it("switching to week emits the week's range around the current anchor", () => {
    const onViewChange = vi.fn();
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        navigation
        locale="en"
        onViewChange={onViewChange}
      />,
    );
    fireEvent.click(getByRole("button", { name: /^week$/i }));
    expect(onViewChange).toHaveBeenCalledWith({
      view: "week",
      from: "2026-07-13T00:00:00",
      to: "2026-07-20T00:00:00",
    });
  });

  it("omits the view switcher when navigationViews is false (period-nav only)", () => {
    const { getByRole, queryByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        navigation
        navigationViews={false}
        locale="en"
      />,
    );
    expect(getByRole("toolbar")).toBeTruthy();
    expect(queryByRole("button", { name: /^week$/i })).toBeNull();
    // prev/today/next are still there.
    expect(getByRole("button", { name: /next/i })).toBeTruthy();
  });
});

describe("AetherCalendar — toolbar period title + i18n", () => {
  it("names the visible month in English", () => {
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} navigation locale="en" />,
    );
    expect(getByRole("toolbar").textContent).toMatch(/July 2026/);
  });

  it("localizes the today button to Spanish (neutral 'tú', no voseo)", () => {
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} navigation locale="es" />,
    );
    expect(within(getByRole("toolbar")).getByRole("button", { name: /^hoy$/i })).toBeTruthy();
  });
});

describe("AetherCalendar — defensive anchor (JS consumers / wrapper default)", () => {
  it("treats a blank anchor as today instead of throwing", () => {
    const { getByRole } = render(<AetherCalendar view="month" anchor="" events={[]} />);
    expect(getByRole("grid")).toBeTruthy();
  });

  it("falls back to today for an unparseable anchor", () => {
    const props = {
      view: "month",
      anchor: "not-a-date",
      events: [],
    } as unknown as React.ComponentProps<typeof AetherCalendar>;
    const { getByRole } = render(<AetherCalendar {...props} />);
    expect(getByRole("grid")).toBeTruthy();
  });
});
