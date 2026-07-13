/**
 * The links the product mails the guest — used exactly as a guest uses them: clicked, verbatim.
 *
 * ⚠️ THIS SPEC IS RED ON PURPOSE (`test.fail`). It documents a live P1 defect that no unit test can
 * see, because the defect lives in the seam BETWEEN two internally-consistent halves:
 *
 *   * the server mints `{base}/cancel?token=…` and `{base}/reschedule?token=…`
 *     (`apps/server/.../services/bookings.py::_guest_link`), and its unit test pins exactly that
 *     shape (`apps/server/tests/test_bookings_service.py:680`);
 *   * the booking page REQUIRES `booking=<uuid>` on `/cancel`, and `booking=<uuid>` +
 *     `event_type=<uuid>` on `/reschedule` (`apps/booking/.../app.py::cancel_form` /
 *     `reschedule_form`), and renders its "missing context" error page otherwise — and its own unit
 *     tests always pass those parameters.
 *
 * Result: **an account-less guest who clicks the link in their confirmation email cannot cancel or
 * reschedule.** RF-09's whole point, broken, with every test green. Root fix: mint the complete URL
 * in `_guest_link` (it has the booking and its event type in hand) and update the pinning unit test.
 *
 * When that lands, these two tests start PASSING — and `test.fail` turns the run RED, forcing the
 * annotation off. The defect cannot be fixed silently, and it cannot be forgotten.
 */

import { expect, test } from "@playwright/test";

import { Api } from "../src/api.js";
import { type GuestLinks, guestLinks, Mail } from "../src/mail.js";
import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();
const api = new Api(stack);
const mail = new Mail(stack);

const guest = {
  name: "Link Checker",
  email: `links-${run.runId}@e2e.test`,
  timezone: "UTC",
};

let mailed: GuestLinks;

test.beforeAll(async () => {
  // Book through the API: the subject here is the mailed link, not the booking UI. Take the LAST
  // offered slot so this never competes with the golden flow for the first one.
  const offered = await api.offeredStarts(run.eventTypeId);
  const start = offered.at(-1);
  if (start === undefined) {
    throw new Error("no slot on offer — cannot produce a confirmation email to read");
  }
  const booking = await api.createBooking({
    event_type_id: run.eventTypeId,
    start,
    guest_name: guest.name,
    guest_email: guest.email,
    guest_timezone: guest.timezone,
    locale: "en",
  });
  expect(booking.status).toBe("confirmed");

  const message = await mail.waitForMessage(guest.email, run.eventTitle);
  mailed = guestLinks(message);
});

test("the mailed CANCEL link opens a page the guest can actually cancel from", async ({ page }) => {
  test.fail(
    true,
    "P1: _guest_link mints /cancel?token=… but the page needs booking=<uuid> too — see the header",
  );

  await page.goto(mailed.cancel); // verbatim: nothing added, nothing repaired
  await expect(page.getByRole("button", { name: /Yes, cancel|Sí, cancelar/ })).toBeVisible();
});

test("the mailed RESCHEDULE link opens a page offering new times", async ({ page }) => {
  test.fail(
    true,
    "P1: _guest_link mints /reschedule?token=… but the page needs booking + event_type too",
  );

  await page.goto(mailed.reschedule); // verbatim
  await expect(page.locator('#slots input[name="new_start"]').first()).toBeAttached();
});
