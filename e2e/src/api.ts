/**
 * A thin, typed client for the AetherCal API (`/api/v1`) — the E2E's *oracle*.
 *
 * The browser drives the product; this client asks the server what actually happened. It is
 * deliberately dumb (raw `fetch`, no retries, no cleverness): a helper that papers over a 500 would
 * launder the very failure the test exists to catch. Any non-2xx throws with the body attached.
 */

import type { StackConfig } from "./stack.js";

export interface Schedule {
  id: string;
  name: string;
  timezone: string;
}

export interface EventType {
  id: string;
  slug: string;
  title: string;
  duration_seconds: number;
}

export interface Slot {
  /** ISO-8601 UTC instant. */
  start: string;
  end: string;
}

export interface SlotsResponse {
  event_type_id: string;
  timezone: string;
  availability: "ok" | "degraded" | "unavailable";
  slots: Slot[];
}

export type BookingStatus = "pending" | "confirmed" | "cancelled" | "no_show";

export interface Booking {
  id: string;
  event_type_id: string;
  start: string;
  end: string;
  status: BookingStatus;
  guest_name: string;
  guest_email: string;
  rescheduled_from_id: string | null;
  cancelled_at: string | null;
}

export interface WebhookCreated {
  id: string;
  url: string;
  events: string[];
  /** Returned exactly once, at creation. */
  secret: string;
}

/** `YYYY-MM-DD` in UTC — the date form the slots API takes for its window. */
export function utcDate(instant: Date): string {
  return instant.toISOString().slice(0, 10);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly method: string,
    readonly path: string,
    readonly body: string,
  ) {
    super(`${method} ${path} → ${status}: ${body.slice(0, 400)}`);
    this.name = "ApiError";
  }
}

export class Api {
  private readonly base: string;
  private readonly key: string;

  constructor(stack: StackConfig) {
    this.base = `${stack.apiUrl}/api/v1`;
    this.key = stack.apiKey;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.key}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    if (!response.ok) {
      throw new ApiError(response.status, method, path, text);
    }
    return (text === "" ? undefined : JSON.parse(text)) as T;
  }

  /** `{"status":"ok"}` — the container's own healthcheck target. */
  async health(): Promise<{ status: string }> {
    const response = await fetch(`${this.base}/health`);
    if (!response.ok) {
      throw new ApiError(response.status, "GET", "/health", await response.text());
    }
    return (await response.json()) as { status: string };
  }

  createSchedule(data: {
    name: string;
    timezone: string;
    rules: Record<string, { start: string; end: string }[]>;
  }): Promise<Schedule> {
    return this.call<Schedule>("POST", "/schedules/", data);
  }

  createEventType(data: {
    host_id: string;
    schedule_id: string;
    slug: string;
    title: string;
    description?: string;
    duration_seconds: number;
    min_notice_seconds: number;
    max_advance_seconds: number;
  }): Promise<EventType> {
    return this.call<EventType>("POST", "/event-types/", data);
  }

  createWebhook(data: { url: string; events: string[] }): Promise<WebhookCreated> {
    return this.call<WebhookCreated>("POST", "/webhooks", data);
  }

  slots(eventTypeId: string, from: string, to: string, tz = "UTC"): Promise<SlotsResponse> {
    const query = new URLSearchParams({ event_type: eventTypeId, from, to, tz });
    return this.call<SlotsResponse>("GET", `/slots/?${query.toString()}`);
  }

  /**
   * Book straight through the API (no browser). Used by specs whose subject is NOT the booking UI —
   * the mailed guest links, the no-show transition — so they do not re-litigate the golden flow.
   */
  createBooking(data: {
    event_type_id: string;
    start: string;
    guest_name: string;
    guest_email: string;
    guest_timezone: string;
    locale?: string;
  }): Promise<Booking> {
    return this.call<Booking>("POST", "/bookings/", data);
  }

  booking(bookingId: string): Promise<Booking> {
    return this.call<Booking>("GET", `/bookings/${bookingId}`);
  }

  listBookings(): Promise<Booking[]> {
    return this.call<Booking[]>("GET", "/bookings/");
  }

  /** The booking a guest made in the browser, found by the run-unique guest email. */
  async bookingByGuestEmail(email: string): Promise<Booking | undefined> {
    const all = await this.listBookings();
    // A rescheduled booking is a NEW row that inherits the guest; the live one is the last created.
    const mine = all.filter((row) => row.guest_email === email);
    return mine.at(-1);
  }

  /**
   * The starts (ISO-8601 UTC) currently on offer for `eventTypeId`, over a window that covers the
   * booking page's own 7-day view. This is the oracle for "the slot was released / is taken".
   */
  async offeredStarts(eventTypeId: string, days = 7): Promise<string[]> {
    const from = new Date();
    const to = new Date(from.getTime() + (days - 1) * 24 * 60 * 60 * 1000);
    const response = await this.slots(eventTypeId, utcDate(from), utcDate(to));
    return response.slots.map((slot) => new Date(slot.start).toISOString());
  }
}
