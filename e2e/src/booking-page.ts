/**
 * The guest's surface: driving the public booking page in a real browser.
 *
 * Every locator here is anchored on markup the page actually emits (`apps/booking/.../views.py`):
 * a slot is an `<a class="slot">` whose `href` carries the exact ISO start (`_slot_link`), the
 * details form posts `name` / `email` / `notes` plus a hidden `start` (`booking_form_page`), and a
 * reschedule time is a POST form carrying a hidden `new_start` (`reschedule_section`). Reading the
 * start out of the DOM — rather than computing it — is what makes "the old slot was released" an
 * assertion about the real booking instead of about our own arithmetic.
 */

import { expect, type Page } from "@playwright/test";

/** Drive the page in English so assertions read against stable copy (`i18n.py`). */
export const LANG = "en";

export interface OfferedSlot {
  /** The ISO-8601 instant the link books, straight out of its `href`. */
  iso: string;
  /** The href to follow to book it. */
  href: string;
}

/** Every slot the page is offering right now, in the order the page lists them. */
export async function offeredSlots(page: Page): Promise<OfferedSlot[]> {
  const hrefs = await page
    .locator("#slots a.slot")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") ?? ""));
  return hrefs
    .map((href) => {
      const start = new URL(href, "http://placeholder.invalid").searchParams.get("start");
      return start === null ? undefined : { iso: new Date(start).toISOString(), href };
    })
    .filter((slot): slot is OfferedSlot => slot !== undefined);
}

/** Open the public event page for `slug` in UTC (so the offered instants are unambiguous). */
export async function openEventPage(page: Page, bookingUrl: string, slug: string): Promise<void> {
  await page.goto(`${bookingUrl}/e/${slug}?tz=UTC&lang=${LANG}`);
  await expect(page.locator("#slots")).toBeVisible();
}

/**
 * Book `slot` from the public page exactly as a guest does: click the time, fill the details form,
 * submit. Returns once the confirmation page has rendered.
 */
export async function bookSlot(
  page: Page,
  slot: OfferedSlot,
  guest: { name: string; email: string; notes?: string },
): Promise<void> {
  await page.locator(`#slots a.slot[href="${slot.href}"]`).click();

  // Step 2 — the details form. The hidden `start` must still be the slot we clicked: if the page
  // lost it between steps, the booking would land on another time and every later assertion would
  // be measuring the wrong booking.
  const start = page.locator('input[name="start"]');
  await expect(start).toHaveCount(1);
  expect(new Date(await start.inputValue()).toISOString()).toBe(slot.iso);

  await page.getByLabel("Full name").fill(guest.name);
  await page.getByLabel("Email").fill(guest.email);
  if (guest.notes !== undefined) {
    await page.getByLabel("Notes (optional)").fill(guest.notes);
  }

  // ==The captcha is part of this form, so filling the form means waiting for it.==
  //
  // The page renders a Turnstile widget whenever it holds a site key, and the widget writes its
  // answer into a hidden `cf-turnstile-response` input ASYNCHRONOUSLY. The API fail-closes on an
  // empty one — no token, no round-trip to Cloudflare, a flat 403 — so a submit that races the
  // script books nothing and the guest is told "Something went wrong". That is precisely what
  // happened the first time this stack ran with the public API switched on.
  //
  // The wait is unconditional on purpose: this stack always configures the always-passes test key
  // (scripts/deploy.env.template), so a missing widget means a broken stack, and this should say so
  // here rather than skip the wait and fail later, further away, as a mystery.
  const captcha = page.locator('input[name="cf-turnstile-response"]');
  await captcha.waitFor({ state: "attached", timeout: 15_000 });
  await expect
    .poll(async () => captcha.inputValue().catch(() => ""), {
      message:
        "the Turnstile widget never issued a token, so the API refuses this booking with a 403 — " +
        "the widget script did not load, or the site key is not the always-passes test key",
      timeout: 15_000,
    })
    .not.toBe("");

  await page.getByRole("button", { name: "Confirm booking" }).click();

  // Step 3 — the confirmation.
  await expect(page.getByRole("heading", { name: /You're all set/i })).toBeVisible();
}
