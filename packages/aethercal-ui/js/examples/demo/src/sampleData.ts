/**
 * Deterministic sample data for the AetherCal public demo (AetherCal-06 §9).
 *
 * The playground needs events that make every one of the four views — and the cross-midnight /
 * overlap / all-day edge cases — look real, regardless of the day the visitor opens it. So the set
 * is generated relative to a supplied `today`: a "this week" cluster anchored to today (so the
 * week/day now-line and the timed events are always relevant) plus a fixed mid-month cluster (so the
 * month grid is populated and one day overflows into "+N more" even near a month boundary).
 *
 * Pure, framework-free, and exhaustively testable (sampleData.test.ts): no DOM, no Date.now inside —
 * `today` is injected so the output is deterministic for a given input.
 */
import type { CalendarEvent, CalendarResource } from "@aethercal/calendar-react";

/** Format a Date as the calendar's naive local wall-time ISO ("YYYY-MM-DDTHH:MM:SS", no offset). */
export function toLocalIso(date: Date): string {
  const p = (n: number): string => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}` +
    `T${p(date.getHours())}:${p(date.getMinutes())}:00`
  );
}

/** Local midnight of `date` (drops the time-of-day). */
function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/** A Date `dayOffset` days from `base`'s midnight, at `hour:minute` local. */
function at(base: Date, dayOffset: number, hour: number, minute = 0): Date {
  return new Date(
    base.getFullYear(),
    base.getMonth(),
    base.getDate() + dayOffset,
    hour,
    minute,
  );
}

/** Restrained, non-AI-slop accents (muted slate / taupe / olive / rose — no cyan, no lavender). */
const ACCENT = {
  slate: "#5b7a8c",
  taupe: "#8a7a5b",
  olive: "#6f7a5b",
  rose: "#8c5b6b",
} as const;

/**
 * Build the demo event set for a given "today". Covers: overlapping timed events (lanes), a
 * cross-midnight timed event, single- and multi-day all-day events, a `+N more` overflow day, and a
 * mix of editable/non-editable and colored/neutral chips.
 */
/**
 * The timeline's rows (RF-28). Two grouped teams plus an ungrouped one, so the demo shows grouping,
 * collapse, AND an ungrouped resource keeping its own place — and `buildSampleEvents` leaves one
 * event unassigned on purpose, so the "unassigned" row is visible rather than a claim in a docstring.
 */
export function buildSampleResources(): CalendarResource[] {
  return [
    { id: "ana", title: "Ana Rivas", groupId: "Consultoría", color: ACCENT.slate },
    { id: "beto", title: "Beto Sosa", groupId: "Consultoría", color: ACCENT.olive },
    { id: "cami", title: "Cami Duarte", groupId: "Producto", color: ACCENT.taupe },
    { id: "dani", title: "Dani Peña", groupId: "Producto", color: ACCENT.rose },
    { id: "sala", title: "Sala de juntas" },
  ];
}

/** Which resource each sample event sits on. Anything absent here rides the "unassigned" row. */
const EVENT_RESOURCE: Readonly<Record<string, string>> = {
  standup: "sala",
  design: "cami",
  oneonone: "ana",
  lunch: "beto",
  onboarding: "ana",
  release: "dani",
  focus: "cami",
  conf: "beto",
  planning: "cami",
  call: "beto",
  webinar: "dani",
  retro: "sala",
  "m-kickoff": "ana",
  "m-workshop": "cami",
  "m-a": "ana",
  "m-b": "beto",
  "m-c": "dani",
  "m-d": "cami",
  "m-launch": "beto",
  "m-holiday": "sala",
  // "m-e" is deliberately left out — the only one — so the unassigned row has exactly what the
  // docstring above promises: one orphan, not three.
};

export function buildSampleEvents(today: Date): CalendarEvent[] {
  const base = startOfDay(today);
  const timed = (
    id: string,
    title: string,
    dayOffset: number,
    startH: number,
    startM: number,
    endH: number,
    endM: number,
    extra: Partial<CalendarEvent> = {},
  ): CalendarEvent => ({
    id,
    title,
    start: toLocalIso(at(base, dayOffset, startH, startM)),
    end: toLocalIso(at(base, dayOffset, endH, endM)),
    revision: 1,
    ...extra,
  });

  // Cross-midnight timed event: starts today 22:00, ends tomorrow 01:30.
  const releaseStart = at(base, 0, 22, 0);
  const releaseEnd = at(base, 1, 1, 30);

  // A fixed mid-month day guaranteed to fall inside the anchor's month (so the month grid is dense
  // even if `today` is near the month edge). The 12th also carries the "+N more" overflow cluster.
  const monthDay = (dayOfMonth: number, hour: number, minute = 0): Date =>
    new Date(base.getFullYear(), base.getMonth(), dayOfMonth, hour, minute);
  const monthTimed = (
    id: string,
    title: string,
    dayOfMonth: number,
    startH: number,
    endH: number,
    extra: Partial<CalendarEvent> = {},
  ): CalendarEvent => ({
    id,
    title,
    start: toLocalIso(monthDay(dayOfMonth, startH)),
    end: toLocalIso(monthDay(dayOfMonth, endH)),
    revision: 1,
    ...extra,
  });

  // One place assigns the resources, so the map above and the events below cannot drift apart.
  const onResources = (list: readonly CalendarEvent[]): CalendarEvent[] =>
    list.map((event) => {
      const resourceId = EVENT_RESOURCE[event.id];
      return resourceId === undefined ? event : { ...event, resourceId };
    });

  return onResources([
    // --- This-week cluster (anchored to today) — drives week/day + the now line ---
    timed("standup", "Standup del equipo", 0, 9, 0, 9, 30, { color: ACCENT.slate }),
    // Overlaps the standup -> exercises the overlap lanes in week/day.
    timed("design", "Revisión de diseño", 0, 9, 15, 10, 15),
    timed("oneonone", "1:1 con Nicolás", 0, 11, 0, 11, 45, { color: ACCENT.olive }),
    timed("lunch", "Almuerzo", 0, 13, 0, 14, 0, { editable: false }),
    timed("onboarding", "Onboarding — cliente nuevo", 0, 15, 0, 16, 30, { color: ACCENT.taupe }),
    {
      id: "release",
      title: "Ventana de release",
      start: toLocalIso(releaseStart),
      end: toLocalIso(releaseEnd),
      color: ACCENT.rose,
      revision: 1,
    },
    // All-day single-day (today) + a multi-day all-day band spanning yesterday..+2.
    { id: "focus", title: "Día de enfoque", start: toLocalIso(at(base, 0, 0, 0)), end: toLocalIso(at(base, 1, 0, 0)), allDay: true, revision: 1 },
    { id: "conf", title: "Conferencia Miami", start: toLocalIso(at(base, -1, 0, 0)), end: toLocalIso(at(base, 2, 0, 0)), allDay: true, color: ACCENT.slate, revision: 1 },
    // Other weekdays around today, so navigating the week always finds content.
    timed("planning", "Planificación de sprint", 1, 10, 0, 11, 0),
    timed("call", "Llamada con proveedor", 2, 14, 0, 14, 30, { color: ACCENT.olive }),
    timed("webinar", "Webinar de producto", 3, 16, 0, 17, 30, { editable: false }),
    timed("retro", "Retrospectiva", -1, 15, 30, 16, 30),

    // --- Fixed mid-month cluster — keeps the month grid populated near either month edge ---
    monthTimed("m-kickoff", "Kickoff de proyecto", 5, 10, 11),
    monthTimed("m-workshop", "Taller de descubrimiento", 9, 13, 15, { color: ACCENT.taupe }),
    // Overflow day: five events on the 12th -> month view collapses into "+N more".
    monthTimed("m-a", "Café con socio", 12, 8, 9),
    monthTimed("m-b", "Demo interna", 12, 10, 11, { color: ACCENT.slate }),
    monthTimed("m-c", "Revisión legal", 12, 12, 13),
    monthTimed("m-d", "Entrega de propuesta", 12, 14, 15, { color: ACCENT.rose }),
    monthTimed("m-e", "Cierre de la semana", 12, 16, 17),
    monthTimed("m-launch", "Lanzamiento", 18, 9, 10, { color: ACCENT.olive }),
    { id: "m-holiday", title: "Feriado", start: toLocalIso(monthDay(22, 0)), end: toLocalIso(monthDay(23, 0)), allDay: true, revision: 1 },
  ]);
}
