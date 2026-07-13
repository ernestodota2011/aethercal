/**
 * Types and small helpers shared by the resource timeline's parts (RF-28).
 *
 * The view is split so that no single file owns geometry AND pointer gestures AND keyboard gestures
 * AND presentation at once — that shape is where bugs hide (four review rounds found theirs there).
 * This module holds only what genuinely crosses those seams; anything used by one part alone stays
 * in that part.
 */
import { parseLocalDateTime } from "@aethercal/calendar-core";
import type { Edge, EventResizePayload, TimelineRow } from "@aethercal/calendar-core";
import type * as React from "react";

/** Allow setting `--ac-*` custom properties (and numeric lane counts) via inline style. */
export type StyleWithVars = React.CSSProperties & Record<`--${string}`, string | number>;

/** The in-flight POINTER gesture, tracked in a ref so window listeners see live data. */
export type Gesture =
  | {
      kind: "resize";
      pointerId: number;
      eventId: string;
      edge: Edge;
      trackEl: HTMLElement;
      payload: EventResizePayload | null;
    }
  | {
      kind: "select";
      pointerId: number;
      resourceId: string;
      trackEl: HTMLElement;
      anchorDate: string;
      anchorMinute: number;
      currentDate: string;
      currentMinute: number;
    };

/**
 * In-flight KEYBOARD gesture. A move tracks BOTH a time target and a RESOURCE target — stepping the
 * resource is the keyboard equivalent of dragging a bar onto another row, and is exactly why
 * `GridPoint` grew a `resourceId` (RF-28). `moved` keeps a confirm-without-moving a strict no-op.
 */
export type KbGrab =
  | {
      kind: "move";
      eventId: string;
      dateOnly: string;
      minute: number;
      resourceId: string;
      moved: boolean;
    }
  | { kind: "resize"; eventId: string; dateOnly: string; minute: number; moved: boolean };

/** The live band drawn while dragging across empty track to create. */
export interface SelectBand {
  resourceId: string;
  leftFraction: number;
  widthFraction: number;
}

export function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export const pct = (fraction: number): string => `${fraction * 100}%`;

export const EMPTY_IDS: ReadonlySet<string> = new Set();

/** The unassigned row's stable key. Namespaced so it can never collide with a real resource id. */
export const UNASSIGNED_KEY = "unassigned";

/** A row's stable key: its resource id (namespaced) or the unassigned sentinel. */
export const rowKeyOf = (row: TimelineRow): string =>
  row.resource ? `r:${row.resource.id}` : UNASSIGNED_KEY;

/** Fraction [0, 1] across a track the pointer's `clientX` falls at (0 for an unmeasured track). */
export function fractionInTrack(clientX: number, trackEl: HTMLElement): number {
  const rect = trackEl.getBoundingClientRect();
  if (!(rect.width > 0)) return 0;
  return (clientX - rect.left) / rect.width;
}

/** Minute-of-day (0..1440) of an ISO local datetime string. */
export function minuteOfDayOf(iso: string): number {
  const d = parseLocalDateTime(iso);
  return d.getHours() * 60 + d.getMinutes();
}

/** A locale time label for a minute-of-day on a given day (handles the 1440 window end gracefully). */
export function minuteToTimeLabel(dateOnly: string, minute: number, locale: string): string {
  const base = parseLocalDateTime(`${dateOnly}T00:00:00`);
  const d = new Date(base.getFullYear(), base.getMonth(), base.getDate(), 0, minute, 0);
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(d);
}
