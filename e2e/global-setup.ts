/**
 * Global setup: prove the stack is really there, then build this run's fixtures.
 *
 * Everything here is fail-closed. An unreachable API, an unreadable mailbox, an unreachable sink, a
 * rejected API key, or an event type that offers zero slots all abort the run with a loud error —
 * because each of them would otherwise turn the suite into a green report about nothing. The design
 * doc calls that the silent no-op; this file is where the suite refuses to commit it.
 */

import { randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

import { Api } from "./src/api.js";
import { Mail } from "./src/mail.js";
import { Sink } from "./src/sink.js";
import { RUN_FILE, type RunContext, stackConfig } from "./src/stack.js";

/** 30-minute events, 08:00–20:00 UTC, every day of the week. */
const DURATION_MINUTES = 30;
const OPEN_FROM = "08:00";
const OPEN_TO = "20:00";
const MAX_ADVANCE_DAYS = 60;

/** Fewer than this and the golden flow could not book, reschedule, and still prove a release. */
const MINIMUM_USABLE_SLOTS = 3;

async function assertBookingPageIsUp(bookingUrl: string): Promise<void> {
  const response = await fetch(`${bookingUrl}/healthz`);
  if (!response.ok) {
    throw new Error(`The booking page is not healthy (${response.status}) at ${bookingUrl}`);
  }
}

/**
 * ==The premise every browser spec rests on — asked HERE, of the page, in the page's own terms.==
 *
 * `/healthz` is a liveness probe, and it answered 200 through an entire run in which every
 * `/e/{slug}` was a 404: the page was perfectly healthy and simply had no business to serve
 * (`AETHERCAL_TENANT_SLUG` unset means "no default — every route must carry `/t/{tenant}/…`"). The
 * suite met that as `locator('#slots')` timing out in the first browser journey, which names
 * neither the cause nor the fix — and the a11y specs met the very same 404 as a clean GREEN, because
 * a "Not found" page has no accessibility violations to report.
 *
 * Health is not readiness to be BOOKED. So this asks the one question the specs are actually about:
 * can a guest open the event we just created? Everything else in this file is fail-closed; this was
 * the hole in it.
 */
async function assertBookingPageServesTheEvent(bookingUrl: string, slug: string): Promise<void> {
  const url = `${bookingUrl}/e/${slug}?tz=UTC&lang=en`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `The booking page answered ${response.status} for ${url}, so no browser spec can book ` +
        "anything. The event type exists and the API offers its slots, so this is the PAGE failing " +
        "to resolve the business — check AETHERCAL_TENANT_SLUG in deploy/.env: unset, the page has " +
        "no default business and answers every unprefixed /e/{slug} with a 404. stack-up.sh writes " +
        "it from the same TENANT_SLUG it creates the tenant with.",
    );
  }
}

export default async function globalSetup(): Promise<void> {
  const stack = stackConfig(); // throws when the stack was never brought up
  const api = new Api(stack);
  const mail = new Mail(stack);
  const sink = new Sink(stack);

  const health = await api.health();
  if (health.status !== "ok") {
    throw new Error(`The API reports ${JSON.stringify(health)} — refusing to test a sick stack.`);
  }
  await assertBookingPageIsUp(stack.bookingUrl);
  await mail.assertReachable();
  await sink.assertReachable();

  // One run must never read another run's email or deliveries.
  await mail.purge();
  await sink.reset();

  const runId = randomBytes(4).toString("hex");
  const rules = Object.fromEntries(
    [0, 1, 2, 3, 4, 5, 6].map((weekday) => [String(weekday), [{ start: OPEN_FROM, end: OPEN_TO }]]),
  );

  // The API key is exercised here, before any spec runs: a rejected key fails setup loudly instead
  // of surfacing later as a mystery 401 in the middle of a browser journey.
  const schedule = await api.createSchedule({ name: `e2e-${runId}`, timezone: "UTC", rules });

  const eventTitle = `E2E intro call ${runId}`;
  const eventType = await api.createEventType({
    host_id: stack.hostUserId,
    schedule_id: schedule.id,
    slug: `e2e-${runId}`,
    title: eventTitle,
    description: "End-to-end test event type.",
    duration_seconds: DURATION_MINUTES * 60,
    min_notice_seconds: 0,
    max_advance_seconds: MAX_ADVANCE_DAYS * 24 * 60 * 60,
  });

  const webhook = await api.createWebhook({
    url: stack.sinkWebhookUrl,
    events: ["booking.created", "booking.cancelled", "booking.rescheduled"],
  });
  if (!webhook.secret) {
    throw new Error("The API created a webhook without returning its secret — cannot verify HMAC.");
  }

  // An event type that offers nothing would make every booking assertion vacuous: the browser would
  // find no slot to click, and a laxer suite would call that "no work to do" and pass.
  const offered = await api.offeredStarts(eventType.id);
  if (offered.length < MINIMUM_USABLE_SLOTS) {
    throw new Error(
      `The bootstrapped event type offers only ${offered.length} slot(s); the golden flow needs at ` +
        `least ${MINIMUM_USABLE_SLOTS}. The schedule (${OPEN_FROM}–${OPEN_TO} UTC daily) or the ` +
        "server clock is wrong — the suite refuses to run on an empty calendar.",
    );
  }

  // The API offers the slots; now prove the PAGE will hand them to a guest. Last, because it is the
  // composite of everything above — the key, the business, the event type and the schedule.
  await assertBookingPageServesTheEvent(stack.bookingUrl, eventType.slug);

  const context: RunContext = {
    runId,
    scheduleId: schedule.id,
    eventTypeId: eventType.id,
    eventSlug: eventType.slug,
    eventTitle,
    durationMinutes: DURATION_MINUTES,
    webhookId: webhook.id,
    webhookSecret: webhook.secret,
  };
  writeFileSync(RUN_FILE, `${JSON.stringify(context, null, 2)}\n`, "utf8");

  process.stdout.write(
    `[e2e] run ${runId}: event type ${eventType.slug} (${offered.length} slots on offer), ` +
      `webhook → ${webhook.url}\n`,
  );
}
