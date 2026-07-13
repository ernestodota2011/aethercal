/**
 * i18n message catalog for the AetherCal calendar (F2-E, AetherCal-06 §7).
 *
 * Weekday/month/date/time NAMES are locale-driven through `Intl` (see labels.ts / timeGridLabels.ts)
 * — nothing there is hardcoded. This module carries the remaining user-facing strings that `Intl`
 * has no primitive for: the all-day / continues / ends / overflow / empty labels, the "view not
 * available" fallback, and the accessible KEYBOARD strings (the grid usage hint plus the live-region
 * announcements for grab / move / resize / drop / create / cancel).
 *
 * It ships English and neutral Spanish ("tú", never voseo — the agency's hard i18n rule) and is
 * deliberately extensible: `resolveMessages` takes an optional registry so a consumer can add locales
 * without forking, and per-string overrides so a single label can be tweaked. Locale lookup falls
 * back from an exact tag ("es-MX") to its primary subtag ("es") to English.
 */
import type { CalendarView } from "@aethercal/calendar-core";

/** Every non-`Intl` user-facing string the calendar renders, including a11y announcements. */
export interface CalendarMessages {
  /** All-day rail / row label. */
  allDay: string;
  /** Time-column label for a day a timed event passes fully through (cross-midnight). */
  continues: string;
  /** Final-day label of a multi-day timed event, from its end time. */
  endsAt: (endTimeLabel: string) => string;
  /** Month-view overflow control ("+N more"). */
  more: (hiddenCount: number) => string;
  /** Agenda/list empty state. */
  noEvents: string;
  /** Fallback for an unrecognized `view` value. */
  unavailable: string;
  /** Keyboard usage hint, exposed on the grid via aria-describedby. */
  keyboardHint: string;
  /** Announced when a move (drag) grab starts on an event. */
  grabbedMoveHint: (title: string) => string;
  /** Announced when a resize grab starts on an event. */
  grabbedResizeHint: (title: string) => string;
  /** Announced as a keyboard move updates the target (day / day+time). */
  movedTo: (label: string) => string;
  /** Announced as a keyboard resize updates the duration/range. */
  resizedTo: (label: string) => string;
  /** Announced when a keyboard move is committed. */
  dropped: (label: string) => string;
  /** Announced when a keyboard resize is committed. */
  resized: (label: string) => string;
  /** Announced when activating an empty slot to create an event. */
  createHere: (label: string) => string;
  /** Announced when a keyboard gesture is cancelled (Escape). */
  cancelled: string;
  /** Accessible name of the built-in navigation toolbar (F2-NAV). */
  navToolbar: string;
  /** Accessible label of the "previous period" button. */
  navPrevious: string;
  /** Accessible label of the "next period" button. */
  navNext: string;
  /** Label of the "jump to today" button. */
  navToday: string;
  /** Display name of each view, for the toolbar's view switcher. */
  viewNames: Record<CalendarView, string>;
}

const en: CalendarMessages = {
  allDay: "All day",
  continues: "Continues",
  endsAt: (t) => `ends ${t}`,
  more: (n) => `+${n} more`,
  noEvents: "No events",
  unavailable: "This view is not available yet.",
  keyboardHint:
    "Use the arrow keys to move between days. Press Enter on an event to grab it, the arrow keys " +
    "to move or resize it, Enter to drop, and Escape to cancel.",
  grabbedMoveHint: (title) =>
    `Grabbed ${title}. Use the arrow keys to move it, Enter to drop, Escape to cancel.`,
  grabbedResizeHint: (title) =>
    `Resizing ${title}. Use the up and down arrow keys to change its duration, Enter to confirm, ` +
    "Escape to cancel.",
  movedTo: (label) => `Moved to ${label}`,
  resizedTo: (label) => `Duration ${label}`,
  dropped: (label) => `Dropped on ${label}`,
  resized: (label) => `Duration set to ${label}`,
  createHere: (label) => `Create an event on ${label}`,
  cancelled: "Cancelled",
  navToolbar: "Calendar navigation",
  navPrevious: "Previous",
  navNext: "Next",
  navToday: "Today",
  viewNames: { month: "Month", week: "Week", day: "Day", list: "Agenda", timeline: "Timeline" },
};

// Neutral Spanish ("tú"). Reviewed to avoid voseo (usá/pulsá/agarrá/soltá…) — a locked test guards it.
const es: CalendarMessages = {
  allDay: "Todo el día",
  continues: "Continúa",
  endsAt: (t) => `termina ${t}`,
  more: (n) => `+${n} más`,
  noEvents: "Sin eventos",
  unavailable: "Esta vista aún no está disponible.",
  keyboardHint:
    "Usa las flechas para moverte entre los días. Pulsa Enter sobre un evento para agarrarlo, las " +
    "flechas para moverlo o cambiar su duración, Enter para soltarlo y Escape para cancelar.",
  grabbedMoveHint: (title) =>
    `Agarraste el evento ${title}. Usa las flechas para moverlo, Enter para soltarlo y Escape ` +
    "para cancelar.",
  grabbedResizeHint: (title) =>
    `Estás cambiando la duración de ${title}. Usa las flechas hacia arriba y abajo para ajustarla, ` +
    "Enter para confirmar y Escape para cancelar.",
  movedTo: (label) => `Movido a ${label}`,
  resizedTo: (label) => `Duración ${label}`,
  dropped: (label) => `Soltado en ${label}`,
  resized: (label) => `Duración establecida en ${label}`,
  createHere: (label) => `Crear un evento en ${label}`,
  cancelled: "Cancelado",
  navToolbar: "Navegación del calendario",
  navPrevious: "Anterior",
  navNext: "Siguiente",
  navToday: "Hoy",
  viewNames: { month: "Mes", week: "Semana", day: "Día", list: "Agenda", timeline: "Cronograma" },
};

/** The built-in locale registry. Extend it by passing your own to `resolveMessages`. */
export const DEFAULT_LOCALE_MESSAGES: Record<string, CalendarMessages> & {
  en: CalendarMessages;
  es: CalendarMessages;
} = { en, es };

/** The primary language subtag of a BCP-47 locale, lowercased ("es-MX" -> "es"). */
function primarySubtag(locale: string): string {
  return locale.toLowerCase().split("-")[0] ?? "";
}

/**
 * Resolve the message pack for a locale, with optional per-string overrides and a custom registry.
 * Lookup order: exact tag → primary subtag → English. Overrides always win over the resolved pack.
 */
export function resolveMessages(
  locale: string,
  overrides?: Partial<CalendarMessages>,
  registry: Record<string, CalendarMessages> = DEFAULT_LOCALE_MESSAGES,
): CalendarMessages {
  const normalized = locale.toLowerCase();
  const base =
    registry[normalized] ??
    registry[primarySubtag(locale)] ??
    registry.en ??
    DEFAULT_LOCALE_MESSAGES.en;
  return overrides ? { ...base, ...overrides } : base;
}
