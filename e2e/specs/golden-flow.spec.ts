/**
 * The golden flow — the one test that crosses all three surfaces and demands they agree (RF-23).
 *
 * A guest books in a real browser on the public page; the API (the admin's truth) is then asked what
 * happened; the outbound webhook (the integrator's truth) is caught and its HMAC verified; the
 * confirmation email (the guest's only copy of their signed links) is read out of a real mailbox.
 * Then the booking is rescheduled and cancelled through the page, and the slot grid is re-checked
 * both in the API and on the page.
 *
 * Every wait has a deadline and throws; every check that could be vacuous carries a negative control
 * (a tampered body must FAIL to verify; a released slot must REAPPEAR). A green run here means the
 * work happened — not that nothing did.
 */

import { expect, test } from "@playwright/test";

import { Api } from "../src/api.js";
import { bookSlot, LANG, offeredSlots, openEventPage } from "../src/booking-page.js";
import { type GuestLinks, guestLinks, Mail } from "../src/mail.js";
import { parseEnvelope, signatureOf, Sink, verifySignature } from "../src/sink.js";
import { runContext, stackConfig } from "../src/stack.js";
import { waitFor } from "../src/wait.js";

const stack = stackConfig();
const run = runContext();
const api = new Api(stack);
const mail = new Mail(stack);
const sink = new Sink(stack);

const guest = {
  name: "E2E Guest",
  email: `guest-${run.runId}@e2e.test`,
  notes: "Booked by the end-to-end suite.",
};

/** State handed from one step of the journey to the next (the steps are one story, run in order). */
const journey: {
  firstBookingId?: string;
  firstStart?: string;
  secondBookingId?: string;
  secondStart?: string;
  confirmationLinks?: GuestLinks;
  rescheduleLinks?: GuestLinks;
  seenMail: Set<string>;
} = { seenMail: new Set() };

function tokenOf(url: string): string {
  const token = new URL(url).searchParams.get("token");
  expect(token, `the mailed link ${url} carries no token`).not.toBeNull();
  return token as string;
}

/**
 * The mailed link, pinned to UTC and English.
 *
 * It adds a display timezone and a locale — nothing else. The link already carries everything the
 * page needs (`services/bookings.py::_guest_link`), and `guest-links.spec.ts` opens it verbatim to
 * keep it that way. This journey only pins tz/lang so the offered instants are unambiguous and the
 * copy assertions read against stable English.
 */
function inUtcEnglish(mailed: string): string {
  const url = new URL(mailed);
  url.searchParams.set("tz", "UTC");
  url.searchParams.set("lang", LANG);
  return url.toString();
}

test.describe.configure({ mode: "serial" });

test.describe("the golden flow", () => {
  test("a guest books an offered slot on the public page", async ({ page }) => {
    await openEventPage(page, stack.bookingUrl, run.eventSlug);

    const offered = await offeredSlots(page);
    expect(
      offered.length,
      "the public page offers no bookable time — there is nothing to test",
    ).toBeGreaterThanOrEqual(3);

    const chosen = offered[0]!;
    await bookSlot(page, chosen, guest);

    // The API is the admin's view of the same event. It must see the booking the browser made.
    const booking = await waitFor(
      `the API to report the booking for ${guest.email}`,
      () => api.bookingByGuestEmail(guest.email),
      { timeoutMs: 20_000 },
    );
    expect(booking.status).toBe("confirmed");
    expect(new Date(booking.start).toISOString()).toBe(chosen.iso);
    expect(booking.guest_name).toBe(guest.name);

    // …and the slot must be gone from what is on offer, in the API and on the page alike.
    const stillOffered = await api.offeredStarts(run.eventTypeId);
    expect(stillOffered, "a booked slot is still being offered by the API").not.toContain(
      chosen.iso,
    );

    await openEventPage(page, stack.bookingUrl, run.eventSlug);
    const afterBooking = await offeredSlots(page);
    expect(
      afterBooking.map((slot) => slot.iso),
      "a booked slot is still being offered by the booking page",
    ).not.toContain(chosen.iso);

    journey.firstBookingId = booking.id;
    journey.firstStart = chosen.iso;
  });

  test("the booking.created webhook arrives, signed with the subscriber's secret", async () => {
    const bookingId = journey.firstBookingId!;

    const delivery = await sink.waitForDelivery("booking.created", bookingId);
    const signature = signatureOf(delivery);
    expect(signature, "the delivery carried no X-AetherCal-Signature header").toBeDefined();

    // The signature is over the exact bytes on the wire — verified as an integrator would.
    expect(
      verifySignature(delivery.body, run.webhookSecret, signature!),
      "the signature does not match the delivered body",
    ).toBe(true);

    // Negative controls. Without these, `verifySignature` could be returning `true` for anything and
    // the assertion above would be theatre.
    const tampered = Buffer.from(delivery.body);
    const flipAt = tampered.length - 2;
    tampered.writeUInt8(tampered.readUInt8(flipAt) ^ 0x01, flipAt);
    expect(verifySignature(tampered, run.webhookSecret, signature!)).toBe(false);
    expect(verifySignature(delivery.body, `${run.webhookSecret}x`, signature!)).toBe(false);

    const envelope = parseEnvelope(delivery);
    expect(envelope.api_version).toBe("1");
    expect(envelope.data.id).toBe(bookingId);
    expect(envelope.data.status).toBe("confirmed");
    expect(delivery.headers["content-type"]).toContain("application/json");
  });

  test("the confirmation email reaches the guest with their signed links", async () => {
    const message = await mail.waitForMessage(guest.email, run.eventTitle, journey.seenMail);
    journey.seenMail.add(message.id);

    const links = guestLinks(message); // throws if either link is absent
    expect(links.cancel).toContain("/cancel?");
    expect(links.reschedule).toContain("/reschedule?");
    expect(tokenOf(links.cancel)).not.toBe(tokenOf(links.reschedule)); // one token per purpose

    journey.confirmationLinks = links;
  });

  test("the guest reschedules — the new slot is taken and the old one is released", async ({
    page,
  }) => {
    const oldBookingId = journey.firstBookingId!;
    const oldStart = journey.firstStart!;
    const links = journey.confirmationLinks!;

    // The link as the guest received it — it carries the signed token AND the booking + event type
    // the page needs (RF-09). We only pin the timezone and the locale.
    const mailedReschedule = new URL(links.reschedule);
    expect(mailedReschedule.searchParams.get("booking")).toBe(oldBookingId);
    expect(mailedReschedule.searchParams.get("event_type")).toBe(run.eventTypeId);

    await page.goto(inUtcEnglish(links.reschedule));
    await expect(page.getByRole("heading", { name: "Reschedule booking" })).toBeVisible();

    // Each offered time is a POST form carrying a hidden `new_start` (views.reschedule_section).
    const starts = await page
      .locator('#slots input[name="new_start"]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("value") ?? ""));
    const candidates = starts
      .map((value) => new Date(value).toISOString())
      .filter((iso) => iso !== oldStart);
    expect(candidates.length, "the reschedule page offers no other time").toBeGreaterThan(0);
    const newStart = candidates[0]!;
    const rawValue = starts.find((value) => new Date(value).toISOString() === newStart)!;

    await page
      .locator(`#slots form:has(input[name="new_start"][value="${rawValue}"]) button.slot`)
      .click();
    await expect(page.getByText("Your booking has been rescheduled.")).toBeVisible();

    // Rescheduling creates a SUCCESSOR row and cancels the predecessor (services/bookings.py).
    const successor = await waitFor(
      "the API to report the rescheduled booking",
      async () => {
        const candidate = await api.bookingByGuestEmail(guest.email);
        return candidate !== undefined && candidate.id !== oldBookingId ? candidate : undefined;
      },
      { timeoutMs: 20_000 },
    );
    expect(successor.status).toBe("confirmed");
    expect(successor.rescheduled_from_id).toBe(oldBookingId);
    expect(new Date(successor.start).toISOString()).toBe(newStart);

    const predecessor = await api.booking(oldBookingId);
    expect(predecessor.status).toBe("cancelled");

    // The heart of it: the old time comes back on offer, the new one leaves it.
    const offeredNow = await api.offeredStarts(run.eventTypeId);
    expect(offeredNow, "the released slot was NOT returned to the offer").toContain(oldStart);
    expect(offeredNow, "the newly booked slot is still on offer").not.toContain(newStart);

    // And the public page agrees — the same truth, on the guest's surface.
    await openEventPage(page, stack.bookingUrl, run.eventSlug);
    const pageStarts = (await offeredSlots(page)).map((slot) => slot.iso);
    expect(pageStarts).toContain(oldStart);
    expect(pageStarts).not.toContain(newStart);

    journey.secondBookingId = successor.id;
    journey.secondStart = newStart;
  });

  test("the reschedule fans out: a signed webhook and a fresh email with new links", async () => {
    const successorId = journey.secondBookingId!;

    const delivery = await sink.waitForDelivery("booking.rescheduled", successorId);
    const signature = signatureOf(delivery);
    expect(signature).toBeDefined();
    expect(verifySignature(delivery.body, run.webhookSecret, signature!)).toBe(true);

    const envelope = parseEnvelope(delivery);
    expect(envelope.data.id).toBe(successorId);
    expect(envelope.data["rescheduled_from_id"]).toBe(journey.firstBookingId);

    const message = await mail.waitForMessage(guest.email, run.eventTitle, journey.seenMail);
    journey.seenMail.add(message.id);
    const links = guestLinks(message);

    // The successor is a NEW booking: its links must not be the predecessor's (a token is bound to
    // one booking, so re-mailing the old ones would leave the guest unable to cancel).
    expect(tokenOf(links.cancel)).not.toBe(tokenOf(journey.confirmationLinks!.cancel));

    journey.rescheduleLinks = links;
  });

  test("the guest cancels — the slot is freed and the cancellation webhook is signed", async ({
    page,
  }) => {
    const bookingId = journey.secondBookingId!;
    const start = journey.secondStart!;

    const mailedCancel = journey.rescheduleLinks!.cancel;
    expect(new URL(mailedCancel).searchParams.get("booking")).toBe(bookingId);

    await page.goto(inUtcEnglish(mailedCancel));
    await expect(page.getByRole("heading", { name: "Cancel booking" })).toBeVisible();
    await page.getByRole("button", { name: "Yes, cancel" }).click();
    await expect(page.getByText("Your booking has been cancelled.")).toBeVisible();

    const cancelled = await api.booking(bookingId);
    expect(cancelled.status).toBe("cancelled");
    expect(cancelled.cancelled_at).not.toBeNull();

    const offeredNow = await api.offeredStarts(run.eventTypeId);
    expect(offeredNow, "a cancelled slot was not released").toContain(start);

    await openEventPage(page, stack.bookingUrl, run.eventSlug);
    expect((await offeredSlots(page)).map((slot) => slot.iso)).toContain(start);

    const delivery = await sink.waitForDelivery("booking.cancelled", bookingId);
    const signature = signatureOf(delivery);
    expect(signature).toBeDefined();
    expect(verifySignature(delivery.body, run.webhookSecret, signature!)).toBe(true);
    expect(parseEnvelope(delivery).data.status).toBe("cancelled");
  });
});
