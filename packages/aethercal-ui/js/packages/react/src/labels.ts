/**
 * Locale-driven label helpers for the month view (AetherCal-06 §7, i18n-ready).
 *
 * F2-A intentionally does NOT ship a preset/translation system (that is F2-E). Instead every
 * user-facing string is derived from the caller's `locale` via `Intl`, or overridable by prop —
 * so nothing is hardcoded in English. The default locale is "en" only as a fallback.
 */
import { type CalendarView, parseLocalDateTime, startOfWeek } from "@aethercal/calendar-core";

// A known Sunday (2023-01-01 has getDay() === 0), used as the base for weekday-name generation.
const REFERENCE_SUNDAY = new Date(2023, 0, 1);

/** Seven short weekday labels for the given locale, ordered from `firstDayOfWeek`. */
export function localeWeekdayLabels(locale: string, firstDayOfWeek: number): string[] {
  const fmt = new Intl.DateTimeFormat(locale, { weekday: "short" });
  return Array.from({ length: 7 }, (_, i) => {
    const dayOfWeek = (firstDayOfWeek + i) % 7;
    const d = new Date(
      REFERENCE_SUNDAY.getFullYear(),
      REFERENCE_SUNDAY.getMonth(),
      REFERENCE_SUNDAY.getDate() + dayOfWeek,
    );
    return fmt.format(d);
  });
}

/** "July 2026"-style month/year title in the caller's locale (used as the grid's accessible name). */
export function formatMonthTitle(anchor: Date, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(anchor);
}

/**
 * The visible-period title for the navigation toolbar (F2-NAV), in the caller's locale:
 * month/list → "July 2026"; week → "Jul 13 – 19, 2026" (a compact day range); day →
 * "Wednesday, July 15, 2026". Purely presentational — everything is derived from `Intl`, nothing
 * hardcoded, so it localizes with `locale` like every other label.
 */
export function formatPeriodTitle(
  view: CalendarView,
  anchor: Date,
  locale: string,
  firstDayOfWeek: number,
): string {
  if (view === "day") {
    return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(anchor);
  }
  if (view === "week") {
    const start = startOfWeek(anchor, firstDayOfWeek);
    const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
    // A compact "Jul 13 – Jul 20, 2026" range; both endpoints localized via Intl (the year lives on
    // the end so a week that straddles a month/year reads correctly).
    const startLabel = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(
      start,
    );
    const endLabel = new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(end);
    return `${startLabel} – ${endLabel}`;
  }
  // month + list.
  return formatMonthTitle(anchor, locale);
}

/** A full, human-readable date label for a day cell (accessible name), in the caller's locale. */
export function formatDayCellLabel(dateOnly: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(parseLocalDateTime(dateOnly));
}

/** A short time-of-day label ("2:00 PM" / "14:00") for an event chip, in the caller's locale. */
export function formatEventTime(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(
    parseLocalDateTime(iso),
  );
}

/**
 * The visible day header for the list/agenda view ("Wednesday, July 15, 2026" /
 * "miércoles, 15 de julio de 2026"), derived from the caller's locale — nothing hardcoded.
 */
export function formatAgendaDayHeading(dateOnly: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(parseLocalDateTime(dateOnly));
}
