/**
 * Theming layer tests (F2-E, AetherCal-06 §7).
 *
 * The color-token VALUES come from the committed `theme-presets.json`, which is generated from the
 * Python `Theme` (the single source of truth; a Python drift test + this file lock the two sides).
 * These tests cover preset resolution, custom-token sanitization, and the default CSS token blocks
 * the base/time-grid stylesheets derive from the same origin.
 */
import { describe, expect, it } from "vitest";
import presets from "./theme-presets.json";
import {
  PRESET_NAMES,
  PRESETS,
  defaultBaseTokenCss,
  defaultTimeGridTokenCss,
  isThemePreset,
  resolveThemeVars,
} from "./theme";

describe("presets", () => {
  it("exposes the four named presets from the generated JSON", () => {
    expect(new Set(PRESET_NAMES)).toEqual(
      new Set(["light", "dark", "midnight", "high_contrast"]),
    );
    expect(PRESETS).toEqual(presets);
  });

  it("every preset carries the same --ac-* token set", () => {
    const keySets = PRESET_NAMES.map((name) => Object.keys(PRESETS[name]).sort().join(","));
    expect(new Set(keySets).size).toBe(1);
    for (const name of PRESET_NAMES) {
      expect(Object.keys(PRESETS[name]).every((k) => k.startsWith("--ac-"))).toBe(true);
    }
  });

  it("dark is a real dark mode, distinct from light", () => {
    expect(PRESETS.dark["--ac-bg"]).not.toBe(PRESETS.light["--ac-bg"]);
    expect(PRESETS.dark["--ac-fg"]).not.toBe(PRESETS.light["--ac-fg"]);
  });
});

describe("isThemePreset", () => {
  it("accepts the preset names and rejects anything else", () => {
    expect(isThemePreset("dark")).toBe(true);
    expect(isThemePreset("neon")).toBe(false);
    expect(isThemePreset(undefined)).toBe(false);
    expect(isThemePreset({ "--ac-fg": "#000" })).toBe(false);
  });
});

describe("resolveThemeVars", () => {
  it("returns no inline vars for undefined (the base stylesheet default = light)", () => {
    expect(resolveThemeVars(undefined)).toEqual({});
  });

  it("returns the full token map for a named preset", () => {
    expect(resolveThemeVars("dark")).toEqual(PRESETS.dark);
    expect(resolveThemeVars("dark")["--ac-bg"]).toBe(PRESETS.dark["--ac-bg"]);
    // A named preset resolves deterministically even for "light" (does not rely on the stylesheet).
    expect(resolveThemeVars("light")).toEqual(PRESETS.light);
  });

  it("ignores an unknown preset name (falls back to the stylesheet default)", () => {
    expect(resolveThemeVars("neon" as never)).toEqual({});
  });

  it("passes through a custom --ac-* token override object", () => {
    expect(resolveThemeVars({ "--ac-event-accent": "#2b6cb0" })).toEqual({
      "--ac-event-accent": "#2b6cb0",
    });
  });

  it("drops keys that are not --ac-* custom properties", () => {
    expect(resolveThemeVars({ color: "red", "--ac-fg": "#111" } as Record<string, string>)).toEqual(
      { "--ac-fg": "#111" },
    );
  });

  it("drops a token value that could break out of a CSS declaration (injection defense)", () => {
    expect(
      resolveThemeVars({ "--ac-bg": "#fff; } body { display:none", "--ac-fg": "#111" }),
    ).toEqual({ "--ac-fg": "#111" });
  });
});

describe("default token CSS blocks (single origin = the light preset)", () => {
  it("base block carries the light color tokens and structural tokens, but not tg-only tokens", () => {
    const css = defaultBaseTokenCss();
    expect(css).toContain(`--ac-fg: ${PRESETS.light["--ac-fg"]};`);
    expect(css).toContain(`--ac-bg: ${PRESETS.light["--ac-bg"]};`);
    expect(css).toContain("--ac-radius:");
    expect(css).toContain("--ac-cell-min-height:");
    // tg-now belongs to the time-grid block, not the base block.
    expect(css).not.toContain("--ac-tg-now:");
  });

  it("time-grid block carries the tg tokens including the light now-line color", () => {
    const css = defaultTimeGridTokenCss();
    expect(css).toContain(`--ac-tg-now: ${PRESETS.light["--ac-tg-now"]};`);
    expect(css).toContain("--ac-tg-gutter:");
    expect(css).toContain("--ac-tg-event-bg: var(--ac-event-bg);");
  });
});
