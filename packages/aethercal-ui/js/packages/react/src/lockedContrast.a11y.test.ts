/**
 * WCAG color-contrast guard for BLOCKED (editable:false) event chips — web-qa finding D-1.
 *
 * A locked chip used to dim the WHOLE chip with `opacity: 0.75`, which fades the fill AND the text
 * together: the muted time label fell to ~3.1:1 on the light preset (< AA 4.5:1). The root fix
 * de-emphasizes only the CHROME — a dashed left accent (a non-color "locked" cue, WCAG 1.4.1) over a
 * fill blended toward the surface (`color-mix`) — and leaves the text colors untouched, so the title
 * and time keep full AA contrast in every preset.
 *
 * jsdom computes no layout, so axe treats every node as not-visible and would mark color-contrast
 * inapplicable (a false green). This test instead locks the two invariants that make the rule pass in
 * a real browser: (1) the `.is-locked` rule no longer uses `opacity` and carries the dashed+blend
 * chrome, and (2) the resulting locked fill keeps BOTH the muted time and the event text ≥ 4.5:1 for
 * all four shipped presets. The live 4-view axe run over the deployed demo is the authoritative check.
 */
import { describe, expect, it } from "vitest";
import { CALENDAR_CSS } from "./styles";
import presetsJson from "./theme-presets.json";
import { TIME_GRID_CSS } from "./timeGridStyles";

type Rgb = [number, number, number];
type Preset = Record<string, string>;
const PRESETS = presetsJson as Record<string, Preset>;

function hexToRgb(hex: string): Rgb {
  const s = hex.replace("#", "");
  return [0, 2, 4].map((i) => Number.parseInt(s.slice(i, i + 2), 16)) as Rgb;
}
function srgbToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function relativeLuminance([r, g, b]: Rgb): number {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}
function contrastRatio(a: Rgb, b: Rgb): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}
/** `color-mix(in srgb, A p%, B)` — a component-wise linear blend of the gamma-encoded sRGB channels. */
function mixSrgb(a: Rgb, b: Rgb, p: number): Rgb {
  return a.map((ai, i) => Math.round(ai * p + b[i]! * (1 - p))) as Rgb;
}

/** The declaration block for the `.is-locked` rule of `baseSelector` (e.g. `.aethercal-event`). */
function lockedRuleBody(css: string, baseSelector: string): string {
  const needle = `${baseSelector}.is-locked`;
  const start = css.indexOf(needle);
  expect(start, `rule not found: ${needle}`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

/** The color-mix percentage declared in a locked rule (throws if the blend is missing). */
function blendRatio(ruleBody: string): number {
  // Non-greedy across any char (incl. the inner `)` of `var(--ac-…)`) to the first percentage.
  const match = ruleBody.match(/color-mix\(in srgb,[\s\S]*?(\d+)%/);
  expect(match, `no color-mix blend in rule: ${ruleBody}`).not.toBeNull();
  return Number(match![1]) / 100;
}

const AA = 4.5;
const LOCKED_RULES = [
  { name: "month chip", css: CALENDAR_CSS, selector: ".aethercal-event" },
  { name: "time-grid block", css: TIME_GRID_CSS, selector: ".aethercal-tg-event" },
] as const;

describe("locked-chip chrome no longer dims the text (D-1)", () => {
  for (const { name, css, selector } of LOCKED_RULES) {
    it(`${name}: .is-locked de-emphasizes chrome, not the whole chip (no opacity)`, () => {
      const body = lockedRuleBody(css, selector);
      expect(body).not.toMatch(/opacity/);
      expect(body).toMatch(/border-left-style:\s*dashed/);
      expect(body).toMatch(/color-mix\(in srgb/);
    });
  }
});

describe("locked-chip text keeps WCAG AA in every preset (D-1)", () => {
  // The month chip and the time-grid block share the same event fill + surface tokens
  // (--ac-tg-event-bg resolves to --ac-event-bg), so one blend ratio drives both.
  const ratio = blendRatio(lockedRuleBody(CALENDAR_CSS, ".aethercal-event"));

  for (const presetName of Object.keys(PRESETS)) {
    it(`${presetName}: locked muted time + event text are both ≥ 4.5:1`, () => {
      const t = PRESETS[presetName]!;
      const lockedFill = mixSrgb(hexToRgb(t["--ac-event-bg"]!), hexToRgb(t["--ac-bg"]!), ratio);
      const mutedText = contrastRatio(hexToRgb(t["--ac-muted"]!), lockedFill);
      const eventText = contrastRatio(hexToRgb(t["--ac-event-fg"]!), lockedFill);
      expect(mutedText, `muted time on locked fill (${presetName})`).toBeGreaterThanOrEqual(AA);
      expect(eventText, `event title on locked fill (${presetName})`).toBeGreaterThanOrEqual(AA);
    });
  }

  it("the old whole-chip opacity:0.75 would have FAILED on the light preset (regression anchor)", () => {
    // Documents WHY the fix is needed: the previous approach blended text+fill toward the white cell
    // and dropped the muted time to ~3.1:1. Guards against a silent revert to `opacity`.
    const t = PRESETS.light!;
    const cell = hexToRgb(t["--ac-cell-bg"]!);
    const dim = (c: Rgb): Rgb => c.map((ci, i) => Math.round(ci * 0.75 + cell[i]! * 0.25)) as Rgb;
    const oldMuted = contrastRatio(dim(hexToRgb(t["--ac-muted"]!)), dim(hexToRgb(t["--ac-event-bg"]!)));
    expect(oldMuted).toBeLessThan(AA);
  });
});
