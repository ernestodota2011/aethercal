/**
 * i18n message-catalog tests (F2-E, AetherCal-06 §7).
 *
 * The date/weekday/time NAMES come from `Intl` (locale-driven, already in labels.ts). This catalog
 * carries the strings `Intl` cannot derive — "All day", "+N more", "Continues", "ends {t}", the
 * empty/agenda message, and the accessible keyboard announcements. It ships `en` + `es` (neutral
 * "tú", never voseo) and is extensible: a caller can pass a registry with more locales or override
 * individual strings.
 */
import { describe, expect, it } from "vitest";
import { DEFAULT_LOCALE_MESSAGES, type CalendarMessages, resolveMessages } from "./i18n";

describe("resolveMessages", () => {
  it("returns English strings for en", () => {
    const m = resolveMessages("en");
    expect(m.allDay).toBe("All day");
    expect(m.more(3)).toBe("+3 more");
    expect(m.endsAt("5:00 PM")).toBe("ends 5:00 PM");
  });

  it("returns neutral Spanish strings for es", () => {
    const m = resolveMessages("es");
    expect(m.allDay).toBe("Todo el día");
    expect(m.continues).toBe("Continúa");
    expect(m.more(3)).toBe("+3 más");
    expect(m.noEvents).toBe("Sin eventos");
  });

  it("resolves a region subtag to its primary language (es-MX -> es)", () => {
    expect(resolveMessages("es-MX").allDay).toBe("Todo el día");
    expect(resolveMessages("EN-US").allDay).toBe("All day");
  });

  it("falls back to English for an unknown locale", () => {
    expect(resolveMessages("fr").allDay).toBe("All day");
  });

  it("lets per-string overrides win over the resolved locale", () => {
    const m = resolveMessages("es", { allDay: "24 h" });
    expect(m.allDay).toBe("24 h");
    // untouched strings keep the locale value
    expect(m.noEvents).toBe("Sin eventos");
  });

  it("is extensible with a custom locale registry", () => {
    const fr: CalendarMessages = {
      ...DEFAULT_LOCALE_MESSAGES.en,
      allDay: "Toute la journée",
      noEvents: "Aucun événement",
    };
    const registry = { ...DEFAULT_LOCALE_MESSAGES, fr };
    expect(resolveMessages("fr", undefined, registry).allDay).toBe("Toute la journée");
    expect(resolveMessages("fr-CA", undefined, registry).noEvents).toBe("Aucun événement");
  });

  it("Spanish never uses voseo (house style: neutral Latin American Spanish)", () => {
    const m = resolveMessages("es");
    const samples = [
      m.allDay,
      m.continues,
      m.noEvents,
      m.unavailable,
      m.keyboardHint,
      m.grabbedMoveHint("Cita"),
      m.grabbedResizeHint("Cita"),
      m.movedTo("lunes"),
      m.resizedTo("1 h"),
      m.dropped("lunes"),
      m.resized("1 h"),
      m.createHere("lunes"),
      m.cancelled,
      m.navToolbar,
      m.navPrevious,
      m.navNext,
      m.navToday,
      ...Object.values(m.viewNames),
    ].join(" ");
    // A blocklist of Río-de-la-Plata voseo imperative/verb forms — none may appear.
    expect(samples).not.toMatch(/\b(usá|pulsá|agarrá|soltá|hacé|tené|poné|movelo|apretá|dale)\b/i);
    // And it does use the neutral "tú" imperative where instructions appear.
    expect(m.keyboardHint.toLowerCase()).toContain("usa");
  });
});
