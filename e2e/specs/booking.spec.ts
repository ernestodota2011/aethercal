/**
 * The booking flow (F-01, gap G-6): the guest's core journey on the public page, proven end to end.
 *
 * The golden flow (`golden-flow.spec.ts`) crosses every surface once, in English. This leg is the
 * focused booking suite it did NOT cover:
 *
 *   * F-01.1 — the happy path is more than "a confirmation rendered": the booking is the API's truth
 *     (the admin sees it), the slot leaves the offer, AND the guest's confirmation e-mail arrives
 *     carrying the `.ics` invite (RF-08). One booking, checked on the three places it must agree.
 *   * F-01.2 — a second booking of a slot someone already took is REFUSED, and refused well: the API
 *     returns 409, the page redirects the guest back to the picker (PRG, so a refresh re-GETs it
 *     instead of re-posting), and a clear localized notice tells them the time is gone. No second
 *     booking is created.
 *   * F-01.3 — the SAME flow completes in both shipped locales (RNF-1: Spanish primary + English),
 *     each ending on its own language's confirmation, not the other's.
 *
 * Every check that could be vacuous carries its opposite: a rejected booking is proven rejected by
 * the API (the guest has NO booking) and by the grid (the slot stays taken), not merely by a banner.
 */

import { expect, test } from "@playwright/test";

import { Api, ApiError } from "../src/api.js";
import { offeredSlots } from "../src/booking-page.js";
import {
  bookFormUrl,
  bookSlotIn,
  confirmationInvite,
  fillAndSubmitDetails,
  openEventPageIn,
  SLOT_UNAVAILABLE_NOTICE,
  unfoldIcs,
} from "../src/booking-flow.js";
import { guestLinks, Mail } from "../src/mail.js";
import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();
const api = new Api(stack);
const mail = new Mail(stack);

test.describe("F-01 · the booking flow", () => {
  test("F-01.1 a guest books an offered slot, and gets a confirmation email with its .ics invite", async ({
    page,
  }) => {
    const guest = {
      name: "Happy Path",
      email: `happy-${run.runId}@e2e.test`,
      notes: "Booked by the booking-flow suite.",
    };

    await openEventPageIn(page, stack.bookingUrl, run.eventSlug, "en");
    const offered = await offeredSlots(page);
    expect(
      offered.length,
      "the public page offers no bookable time — there is nothing to test",
    ).toBeGreaterThanOrEqual(1);
    const chosen = offered[0]!;

    await bookSlotIn(page, chosen, guest, "en");
    // The confirmation names the address the details will be sent to — the guest we just booked.
    await expect(page.getByText(guest.email)).toBeVisible();

    // The API is the admin's view of the same event: it must see the booking the browser made, and
    // stop offering the slot — on the same window the page shows.
    const booking = await api.bookingByGuestEmail(guest.email);
    expect(booking, "the API never reported the booking the browser made").toBeDefined();
    expect(booking!.status).toBe("confirmed");
    expect(new Date(booking!.start).toISOString()).toBe(chosen.iso);
    expect(
      await api.offeredStarts(run.eventTypeId),
      "a booked slot is still on offer",
    ).not.toContain(chosen.iso);

    // The confirmation e-mail is the guest's ONLY copy of their signed links (RF-09) — and it must
    // carry the calendar invite (RF-08). It is an outbox intent drained by the scheduler's ≤60 s
    // tick, so `waitForMessage` waits for it.
    const message = await mail.waitForMessage(guest.email, run.eventTitle);
    const links = guestLinks(message); // throws if either signed link is missing
    expect(links.cancel).toContain("/cancel?");
    expect(links.reschedule).toContain("/reschedule?");

    // The `.ics` invite, un-folded, tied to THIS booking so the check cannot pass on a stray invite.
    const ics = unfoldIcs(await confirmationInvite(stack.mailpitUrl, message.id));
    expect(ics, "the attachment is not an iCalendar object").toContain("BEGIN:VCALENDAR");
    expect(ics, "a confirmation must iTIP-REQUEST the event (RF-08)").toContain("METHOD:REQUEST");
    expect(ics).toContain("BEGIN:VEVENT");
    expect(ics, "a confirmation's event must be CONFIRMED").toContain("STATUS:CONFIRMED");
    expect(ics, "the invite is not for this event").toContain(`SUMMARY:${run.eventTitle}`);
    // `mailto:{email}` — the ATTENDEE line's value form (invite.py `vCalAddress(f"mailto:{email}")`),
    // not a bare substring that a stray occurrence of the address elsewhere could satisfy.
    expect(ics, "the guest is not the invite's attendee").toContain(`mailto:${guest.email}`);
  });

  test("F-01.2 a second booking of a taken slot is rejected on the page and never created", async ({
    page,
  }) => {
    const occupant = {
      name: "First In",
      email: `occupant-${run.runId}@e2e.test`,
    };
    const latecomer = {
      name: "Too Late",
      email: `latecomer-${run.runId}@e2e.test`,
    };

    // Take a real, currently-offered slot so the conflict below is a genuine 409, not a stale start.
    await openEventPageIn(page, stack.bookingUrl, run.eventSlug, "en");
    const offered = await offeredSlots(page);
    expect(offered.length, "the public page offers no bookable time").toBeGreaterThanOrEqual(1);
    const contested = offered[0]!;

    // Occupy it through the admin API — the setup here is "the slot is taken"; the subject under test
    // is the SECOND guest's browser experience, below.
    const held = await api.createBooking({
      event_type_id: run.eventTypeId,
      start: contested.iso,
      guest_name: occupant.name,
      guest_email: occupant.email,
      guest_timezone: "UTC",
      locale: "en",
    });
    expect(held.status, "the setup booking did not take the slot").toBe("confirmed");

    // The conflict IS a 409 at the API contract, not merely "some error": a direct second booking of
    // the SAME instant is rejected with 409 (services/bookings.py maps the partial-index conflict to
    // SlotUnavailableError → api/bookings.py returns 409 `slot_unavailable`). This pins the status the
    // page's PRG below hinges on — `_complete_booking` only redirects with `err=slot_unavailable` for
    // a 409, so without this a non-409 rejection could regress silently behind the same-looking UX.
    let conflictStatus: number | undefined;
    try {
      await api.createBooking({
        event_type_id: run.eventTypeId,
        start: contested.iso,
        guest_name: "Duplicate",
        guest_email: `dupe-${run.runId}@e2e.test`,
        guest_timezone: "UTC",
        locale: "en",
      });
    } catch (error) {
      conflictStatus = error instanceof ApiError ? error.status : undefined;
    }
    expect(conflictStatus, "a duplicate booking of the same slot was not rejected with 409").toBe(
      409,
    );

    // The latecomer re-enters step 2 for the SAME instant directly — the slot has left the picker, so
    // there is no link left to click — fills valid details, and submits into the conflict.
    await page.goto(bookFormUrl(stack.bookingUrl, run.eventSlug, contested.iso, "en"));
    await fillAndSubmitDetails(page, latecomer);

    // PRG: the 409 sends the guest BACK to the picker (a refresh re-GETs it, never re-posts). The
    // notice names the problem; the URL proves it was the conflict redirect, not an inline re-render.
    const notice = page.locator(".notice.error");
    await expect(notice).toBeVisible();
    await expect(notice).toHaveText(SLOT_UNAVAILABLE_NOTICE.en);
    expect(page.url(), "the guest was not PRG-redirected to the picker").toContain(
      "err=slot_unavailable",
    );
    // We are back on the picker (its heading is the event title), NOT on a confirmation.
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(run.eventTitle);

    // ==The negative control the banner is worthless without.== A rejected booking is only rejected
    // if no booking exists for the latecomer and the slot is still taken by the occupant.
    expect(
      await api.bookingByGuestEmail(latecomer.email),
      "the rejected booking was created anyway",
    ).toBeUndefined();
    expect(
      await api.offeredStarts(run.eventTypeId),
      "the contested slot was freed — the conflict did not hold",
    ).not.toContain(contested.iso);
  });

  for (const locale of ["es", "en"] as const) {
    test(`F-01.3 the booking flow completes in ${locale}`, async ({ page }) => {
      const guest = {
        name: `Locale ${locale}`,
        email: `${locale}-${run.runId}@e2e.test`,
      };

      await openEventPageIn(page, stack.bookingUrl, run.eventSlug, locale);
      const offered = await offeredSlots(page);
      expect(offered.length, `the page offers no bookable time in ${locale}`).toBeGreaterThanOrEqual(
        1,
      );
      const chosen = offered[0]!;

      // `bookSlotIn` asserts the page is in `locale` (its <html lang>) AND that the confirmation
      // heading is THIS locale's, not the other language's — the ES/EN parity has teeth.
      await bookSlotIn(page, chosen, guest, locale);

      const booking = await api.bookingByGuestEmail(guest.email);
      expect(booking, `the ${locale} booking never reached the API`).toBeDefined();
      expect(booking!.status).toBe("confirmed");
      expect(new Date(booking!.start).toISOString()).toBe(chosen.iso);
    });
  }
});
