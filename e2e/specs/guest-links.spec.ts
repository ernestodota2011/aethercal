/**
 * The links the product mails the guest — used exactly as a guest uses them: clicked, verbatim.
 *
 * These two were RED on purpose (`test.fail`) until `main` 7a8d336. The server minted
 * `{base}/cancel?token=…` while the booking page reads `booking=<uuid>` — and `event_type=<uuid>` to
 * reschedule — and otherwise renders its "missing context" error, so **no guest could ever cancel or
 * reschedule from their confirmation email**: the whole of RF-09. Each half was internally
 * consistent and unit-tested; the defect lived in the seam between them, where only a test that
 * crosses it can look. `_guest_link` now mints the full context, and these tests are green.
 *
 * They stay as the regression guard. Nothing else in the suite opens a mailed link **verbatim**, and
 * that verbatim click is exactly the act that was broken. Never "repair" the URL here: the moment
 * this spec adds a query parameter of its own, it stops testing the thing it exists to test.
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
  await page.goto(mailed.cancel); // verbatim: nothing added, nothing repaired
  await expect(page.getByRole("button", { name: /Yes, cancel|Sí, cancelar/ })).toBeVisible();
});

test("the mailed RESCHEDULE link opens a page offering new times", async ({ page }) => {
  await page.goto(mailed.reschedule); // verbatim
  await expect(page.locator('#slots input[name="new_start"]').first()).toBeAttached();
});
