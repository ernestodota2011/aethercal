/**
 * Locale-driven label helpers specific to the week/day time-grid views (AetherCal-06 §5/§7).
 *
 * Kept separate from the month view's `labels.ts` so F2-B adds no lines to a shared F2-A/F2-C file.
 * Like the month labels, every user-facing string is derived from the caller's `locale` via `Intl`
 * (nothing hardcoded in English) except the "all day" rail label, which has no `Intl` primitive and
 * is therefore an overridable prop defaulting to English — the full i18n preset system is F2-E.
 */
import { parseLocalDateTime } from "@aethercal/calendar-core";

/** English fallback for the all-day rail label; overridable via the `allDayLabel` prop (i18n → F2-E). */
export const DEFAULT_ALL_DAY_LABEL = "All day";

/** English fallback for the "passes fully through this day" label of a multi-day timed event. */
export const DEFAULT_CONTINUES_LABEL = "Continues";

/** English fallback formatter for a multi-day timed event's final-day label ("ends {end time}"). */
export const defaultFormatEndsLabel = (endTimeLabel: string): string => `ends ${endTimeLabel}`;

/** A day-column header like "Wed 15" in the caller's locale (weekday + day-of-month). */
export function formatDayColumnHeader(dateOnly: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric" }).format(
    parseLocalDateTime(dateOnly),
  );
}

/** A compact hour-axis label ("9 AM" / "09") for the gutter, in the caller's locale. */
export function formatHourLabel(hour: number, locale: string): string {
  // Any date works; only the hour is formatted. Use a fixed reference day to avoid DST edges.
  return new Intl.DateTimeFormat(locale, { hour: "numeric" }).format(new Date(2001, 0, 1, hour));
}

/** The accessible name for the whole grid: a single full date (day view) or a date range (week). */
export function formatTimeGridTitle(dateOnlys: readonly string[], locale: string): string {
  if (dateOnlys.length === 0) return "";
  const first = parseLocalDateTime(dateOnlys[0]!);
  if (dateOnlys.length === 1) {
    return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(first);
  }
  const last = parseLocalDateTime(dateOnlys[dateOnlys.length - 1]!);
  const fmt = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", year: "numeric" });
  return `${fmt.format(first)} – ${fmt.format(last)}`;
}
