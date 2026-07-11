/**
 * Theme application across the views (F2-E, AetherCal-06 §7).
 *
 * A `theme` prop (a preset name or a custom `--ac-*` object) is resolved once by AetherCalendar and
 * applied as INLINE CSS variables on each view's root, so a per-instance theme wins over the base
 * stylesheet without any global class. This locks the visible outcome of every preset (a lightweight,
 * deterministic "snapshot" of the applied tokens) and the default (no inline vars) / custom-object /
 * time-grid cases.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AetherCalendar } from "./AetherCalendar";
import { PRESET_NAMES, PRESETS } from "./theme";

afterEach(cleanup);

const ANCHOR = "2026-07-15";
const NOW = "2026-07-15T09:00:00";

/** The inline value of a CSS custom property on an element. */
function cssVar(el: HTMLElement, name: string): string {
  return el.style.getPropertyValue(name).trim();
}

describe("preset application on the month grid (snapshot of applied tokens)", () => {
  it.each(PRESET_NAMES)("applies every %s token inline on the root", (preset) => {
    const { getByRole } = render(
      <AetherCalendar view="month" anchor={ANCHOR} events={[]} theme={preset} />,
    );
    const root = getByRole("grid");
    // The full resolved token map is present inline and matches the generated preset exactly.
    for (const [name, value] of Object.entries(PRESETS[preset])) {
      expect(cssVar(root, name)).toBe(value);
    }
  });

  it("dark really inverts the surface vs light", () => {
    const dark = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} theme="dark" />);
    expect(cssVar(dark.getByRole("grid"), "--ac-bg")).toBe(PRESETS.dark["--ac-bg"]);
    expect(cssVar(dark.getByRole("grid"), "--ac-bg")).not.toBe(PRESETS.light["--ac-bg"]);
  });
});

describe("default and custom themes", () => {
  it("applies NO inline vars by default (the stylesheet carries the light default)", () => {
    const { getByRole } = render(<AetherCalendar view="month" anchor={ANCHOR} events={[]} />);
    expect(cssVar(getByRole("grid"), "--ac-bg")).toBe("");
    expect(cssVar(getByRole("grid"), "--ac-fg")).toBe("");
  });

  it("applies a custom --ac-* override object inline", () => {
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        theme={{ "--ac-event-accent": "#2b6cb0" }}
      />,
    );
    expect(cssVar(getByRole("grid"), "--ac-event-accent")).toBe("#2b6cb0");
  });

  it("ignores an unsafe custom token value (CSS-injection defense)", () => {
    const { getByRole } = render(
      <AetherCalendar
        view="month"
        anchor={ANCHOR}
        events={[]}
        theme={{ "--ac-bg": "#fff; } body { display:none", "--ac-fg": "#101010" }}
      />,
    );
    expect(cssVar(getByRole("grid"), "--ac-bg")).toBe(""); // dropped
    expect(cssVar(getByRole("grid"), "--ac-fg")).toBe("#101010"); // kept
  });
});

describe("preset application on the other views", () => {
  it("applies the preset on the week time grid AND keeps the tg column count var", () => {
    const { getByRole } = render(
      <AetherCalendar view="week" anchor={ANCHOR} now={NOW} events={[]} theme="midnight" />,
    );
    const root = getByRole("grid");
    expect(cssVar(root, "--ac-bg")).toBe(PRESETS.midnight["--ac-bg"]);
    expect(cssVar(root, "--ac-tg-now")).toBe(PRESETS.midnight["--ac-tg-now"]);
    expect(cssVar(root, "--ac-tg-cols")).toBe("7"); // structural var still set
  });

  it("applies the preset on the list/agenda root", () => {
    const { container } = render(
      <AetherCalendar
        view="list"
        anchor={ANCHOR}
        events={[{ id: "e1", title: "Cita", start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00" }]}
        theme="high_contrast"
      />,
    );
    const root = container.querySelector<HTMLElement>('[data-view="list"]')!;
    expect(cssVar(root, "--ac-bg")).toBe(PRESETS.high_contrast["--ac-bg"]);
    expect(cssVar(root, "--ac-fg")).toBe(PRESETS.high_contrast["--ac-fg"]);
  });
});
