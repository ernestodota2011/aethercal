/**
 * WCAG 2.5.3 "Label in Name" regression guard (web-qa finding M-2).
 *
 * In a real browser, axe's `label-content-name-mismatch` fired on every month `role="gridcell"` that
 * also shows events: the cell's author `aria-label` (the date) does not contain its visible "day
 * number + event" text. It is a semantic false positive of WCAG 2.5.3 (that criterion targets
 * voice-activatable controls; a gridcell is arrow-navigated within a composite grid, and its
 * interactive children — the event chips — already satisfy it), but we fix it at the ROOT rather than
 * suppress it: the cell now takes its accessible name from CONTENT, led by a visually-hidden full-date
 * span, so no author name competes with the visible text and the rule becomes inapplicable.
 *
 * axe cannot be used as the in-repo check here because jsdom computes no layout, so axe treats every
 * element as not-visible-on-screen and marks the rule inapplicable regardless of markup (a false
 * green). This test instead locks the DOM INVARIANTS that make the rule inapplicable in a browser;
 * the live 4-view axe run over the deployed demo is the authoritative confirmation.
 */
import type { CalendarEvent } from "@aethercal/calendar-core";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AetherCalendar } from "./AetherCalendar";

afterEach(cleanup);

const ANCHOR = "2026-07-15";
const events: CalendarEvent[] = [
  { id: "e1", title: "Consulta", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" },
  { id: "e2", title: "Reunión", start: "2026-07-16T11:00:00", end: "2026-07-16T12:30:00" },
];

describe("month gridcells avoid the label-content-name-mismatch trigger (M-2)", () => {
  it("no month gridcell carries an author aria-label/aria-labelledby (name comes from content)", () => {
    const { getAllByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={events} onEventDrop={() => {}} />,
    );
    for (const cell of getAllByRole("gridcell")) {
      expect(cell.getAttribute("aria-label")).toBeNull();
      expect(cell.getAttribute("aria-labelledby")).toBeNull();
    }
  });

  it("each cell still exposes the full localized date to screen readers, with the day number hidden", () => {
    const { container } = render(
      <AetherCalendar view="month" anchor={ANCHOR} locale="en" events={events} />,
    );
    const cell = container.querySelector('[data-date="2026-07-15"]') as HTMLElement;
    // A visually-hidden full-date span leads the cell's accessible name.
    const srDate = cell.querySelector(".aethercal-sr-only");
    expect(srDate?.textContent).toContain("July 15, 2026");
    // The visible number is hidden from the a11y tree (the date already speaks it), so it is not
    // announced twice.
    const number = cell.querySelector(".aethercal-day-number") as HTMLElement;
    expect(number.textContent).toBe("15");
    expect(number.getAttribute("aria-hidden")).toBe("true");
  });

  // The interactive event chips (role="button") carry an `aria-label` = "<time> <title>". Their
  // visible text must CONTAIN that whole string (WCAG 2.5.3), including the space between the time and
  // the title — the visual gap is a flex `gap`, not a text node, so a real whitespace text node has to
  // sit between them or axe sees "9:00Consulta" ⊄ "9:00 Consulta". This mirrors axe's check in jsdom,
  // which (unlike layout) computes textContent faithfully.
  const norm = (s: string | null | undefined): string => (s ?? "").replace(/\s+/g, " ").trim();

  for (const [view, extra] of [
    ["month", { onEventDrop: () => {} }],
    ["week", { now: "2026-07-15T09:00:00", onEventDrop: () => {} }],
  ] as const) {
    it(`the ${view} view's event chip's visible text is contained in its accessible name`, () => {
      const { container } = render(
        <AetherCalendar view={view} anchor={ANCHOR} locale="en" events={events} {...extra} />,
      );
      const chip = container.querySelector('[data-event-id="e1"]') as HTMLElement;
      expect(chip.getAttribute("role")).toBe("button");
      const name = norm(chip.getAttribute("aria-label")); // e.g. "9:00 AM Consulta"
      const visible = norm(chip.textContent); // "9:00 AM Consulta" (needs the whitespace text node)
      expect(visible.length).toBeGreaterThan(0);
      expect(name).toContain(visible);
    });
  }
});
