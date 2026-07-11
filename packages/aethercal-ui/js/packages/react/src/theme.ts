/**
 * Theming layer for the AetherCal calendar (F2-E, AetherCal-06 §7).
 *
 * The calendar is themed entirely through `--ac-*` CSS custom properties. The COLOR token values
 * live in `theme-presets.json`, which is generated from the Python `Theme` model (the single source
 * of truth; a Python drift test + `theme.test.ts` lock the two languages). This module is the TS
 * consumer of that origin: it exposes the four presets, resolves a `theme` prop (a preset name OR a
 * custom `--ac-*` override object) into inline CSS variables to apply on a view's root, and emits
 * the default token blocks the base/time-grid stylesheets inject — so every token value flows from
 * one place. Only STRUCTURAL tokens (font, radii, grid sizes) are TS-owned constants: they are
 * layout, not color, and are identical across every preset.
 *
 * The default look is neutral-premium (no lavender/violet/cyan accents, no glows —
 * reference_anti_ai_slop_doctrina); a genuine dark mode ships as a preset, and the AetherLogik brand
 * is applied by passing a custom theme, never hardcoded into the OSS component.
 */
import presetsJson from "./theme-presets.json";

/** The four shipped presets (AetherCal-06 §7). */
export type ThemePreset = "light" | "dark" | "midnight" | "high_contrast";

/** A map of `--ac-*` CSS custom property → value. */
export type ThemeTokens = Record<string, string>;

/** What a consumer may pass as `theme`: a preset name, a custom token override object, or nothing. */
export type ThemeInput = ThemePreset | ThemeTokens | undefined;

/** The preset color-token maps, sourced from the generated JSON (Python is the origin of values). */
export const PRESETS = presetsJson as Record<ThemePreset, ThemeTokens>;

/** The preset names, in a stable order. */
export const PRESET_NAMES: readonly ThemePreset[] = ["light", "dark", "midnight", "high_contrast"];

const PRESET_NAME_SET: ReadonlySet<string> = new Set(PRESET_NAMES);

/**
 * STRUCTURAL base tokens (constant across presets): the calendar's typeface, corner radius, and the
 * month cell min-height. Layout, not color — so they are not part of the themeable `Theme` contract.
 */
export const STRUCTURAL_BASE_TOKENS: ThemeTokens = {
  "--ac-font":
    'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  "--ac-radius": "8px",
  "--ac-cell-min-height": "96px",
};

/**
 * STRUCTURAL time-grid tokens (constant across presets): the gutter width, body height, hour-row
 * min-height, and the DERIVED tokens that reference base color tokens via `var()` (so the time grid
 * follows the active theme's border/event colors without duplicating them per preset).
 */
export const STRUCTURAL_TG_TOKENS: ThemeTokens = {
  "--ac-tg-gutter": "56px",
  "--ac-tg-body-height": "640px",
  "--ac-tg-hour-min-height": "44px",
  "--ac-tg-line": "var(--ac-border)",
  "--ac-tg-event-bg": "var(--ac-event-bg)",
  "--ac-tg-event-fg": "var(--ac-event-fg)",
  "--ac-tg-event-accent": "var(--ac-event-accent)",
};

/** The one themeable token that lives in the time-grid block (its default comes from the presets). */
const TG_THEMEABLE_TOKENS: readonly string[] = ["--ac-tg-now"];

/** A `--ac-*` value is CSS-safe when it cannot break out of a `--token: value;` declaration. */
const UNSAFE_VALUE = /[;{}<>]/;

/** True for one of the four preset names. */
export function isThemePreset(value: unknown): value is ThemePreset {
  return typeof value === "string" && PRESET_NAME_SET.has(value);
}

/** Serialize a token map to indented `--key: value;` CSS declarations (one per line). */
export function themeTokensToCss(tokens: ThemeTokens): string {
  return Object.entries(tokens)
    .map(([key, value]) => `  ${key}: ${value};`)
    .join("\n");
}

/** The light preset's base color tokens (everything except the time-grid-only tokens). */
function lightBaseColorTokens(): ThemeTokens {
  const out: ThemeTokens = {};
  for (const [key, value] of Object.entries(PRESETS.light)) {
    if (!TG_THEMEABLE_TOKENS.includes(key)) out[key] = value;
  }
  return out;
}

/** The light preset's time-grid-only color tokens. */
function lightTimeGridColorTokens(): ThemeTokens {
  const out: ThemeTokens = {};
  for (const key of TG_THEMEABLE_TOKENS) {
    const value = PRESETS.light[key];
    if (value !== undefined) out[key] = value;
  }
  return out;
}

/**
 * The default `:where(.aethercal-calendar)` token declarations — structural base tokens plus the
 * light preset's base color tokens. This is the DEFAULT (light) look, sourced from the same origin
 * as every other preset, so there is no second hardcoded copy of the token values.
 */
export function defaultBaseTokenCss(): string {
  return themeTokensToCss({ ...STRUCTURAL_BASE_TOKENS, ...lightBaseColorTokens() });
}

/**
 * The default `:where(.aethercal-timegrid)` token declarations — structural time-grid tokens plus
 * the light preset's time-grid color token(s). Same single origin as the base block.
 */
export function defaultTimeGridTokenCss(): string {
  return themeTokensToCss({ ...STRUCTURAL_TG_TOKENS, ...lightTimeGridColorTokens() });
}

/** Keep only well-formed `--ac-*` custom properties with CSS-safe values (injection defense). */
function sanitizeTokens(tokens: ThemeTokens): ThemeTokens {
  const out: ThemeTokens = {};
  for (const [key, value] of Object.entries(tokens)) {
    if (!key.startsWith("--ac-")) continue;
    if (typeof value !== "string" || value.trim() === "" || UNSAFE_VALUE.test(value)) continue;
    out[key] = value;
  }
  return out;
}

/**
 * Resolve a `theme` prop into the inline CSS variables to apply on a view's root element:
 * - `undefined` → `{}` (the base stylesheet already carries the default/light tokens).
 * - a preset name → that preset's full color-token map (deterministic, independent of any ambient
 *   overrides); an unrecognized name falls back to `{}` (the stylesheet default).
 * - a custom object → its `--ac-*` overrides, sanitized against CSS injection.
 */
export function resolveThemeVars(theme: ThemeInput): ThemeTokens {
  if (theme === undefined) return {};
  if (typeof theme === "string") {
    return isThemePreset(theme) ? { ...PRESETS[theme] } : {};
  }
  return sanitizeTokens(theme);
}
