/**
 * The booking-flow surface: driving the public booking form in a real browser, in EITHER locale,
 * and reading the confirmation e-mail's `.ics` invite.
 *
 * This is the locale-general sibling of `booking-page.ts`. That module's `bookSlot` is pinned to
 * English (`getByLabel("Full name")`, `getByRole("heading", { name: /You're all set/i })`) because
 * the golden flow asserts against stable English copy. The booking leg (F-01) instead has to prove
 * the SAME flow works in both shipped locales (RNF-1), so every control here is anchored on markup
 * that does not change with the language:
 *
 *   * the details form posts `name` / `email` / `notes` — those `name` attributes are the wire
 *     contract (`views.booking_form_page` → `_labelled_field`), identical in ES and EN, whereas the
 *     visible labels are localized. Filling by the `name` is filling the field that actually posts.
 *   * the confirmation is asserted structurally (the page's `lang` attribute) PLUS the one localized
 *     string that proves the copy rendered in the right language — the same discipline the golden
 *     flow uses when it pins "You're all set".
 *
 * The `.ics` reader exists because the confirmation e-mail carries the calendar invite as a
 * `text/calendar` attachment (`integrations/smtp/compose.py` → `invite.py`), and the `Mail` helper's
 * text view does not expose attachments — so this reads them straight off Mailpit.
 */

import { expect, type Page } from "@playwright/test";

import type { OfferedSlot } from "./booking-page.js";

/** The two shipped locales (i18n.py `SUPPORTED_LOCALES`). */
export type BookingLocale = "en" | "es";

export interface Guest {
  name: string;
  email: string;
  notes?: string;
}

/**
 * The one localized string that proves the confirmation rendered in the intended language — the
 * `confirmed_heading` prefix for each locale (i18n.py). Anchored on the invariant opening, not the
 * whole sentence, so a wording tweak downstream of it does not turn this into a brittle string diff.
 */
const CONFIRMED_HEADING: Record<BookingLocale, RegExp> = {
  en: /You're all set/,
  es: /¡Listo!/,
};

/** The error banner shown on the picker after a 409 slot conflict PRG (`error_slot_unavailable`). */
export const SLOT_UNAVAILABLE_NOTICE: Record<BookingLocale, string> = {
  en: "That time is no longer available. Please pick another.",
  es: "Ese horario ya no está disponible. Elige otro, por favor.",
};

/** Open the public event page for `slug` in UTC and `locale` (so the offered instants are stable). */
export async function openEventPageIn(
  page: Page,
  bookingUrl: string,
  slug: string,
  locale: BookingLocale,
): Promise<void> {
  await page.goto(`${bookingUrl}/e/${slug}?tz=UTC&lang=${locale}`);
  await expect(page.locator("#slots")).toBeVisible();
}

/**
 * The details-form URL for one `startIso` — the same target a slot link points at (`_slot_link`).
 *
 * Used to re-enter step 2 for a specific instant DIRECTLY, even after that slot has left the picker
 * because someone already took it. That is the only way to drive the double-booking case in a
 * browser: once a slot is booked the page stops offering it, so there is no link left to click.
 */
export function bookFormUrl(
  bookingUrl: string,
  slug: string,
  startIso: string,
  locale: BookingLocale,
): string {
  const query = new URLSearchParams({ start: startIso, tz: "UTC", lang: locale });
  return `${bookingUrl}/e/${slug}/book?${query.toString()}`;
}

/**
 * Fill the details form (structurally, by field `name`) and submit — WITHOUT asserting the outcome.
 *
 * The caller decides what the submit should produce: the confirmation page (a free slot) or the
 * 409-conflict PRG back to the picker (a taken slot). Waiting for the captcha token is mandatory and
 * unconditional for the same reason as `booking-page.bookSlot`: the stack always configures the
 * always-passes Turnstile test key, the widget writes its answer asynchronously, and the API
 * fail-closes on an empty `cf-turnstile-response` with a 403 — so a submit that races the widget
 * books nothing and every downstream assertion measures a failure that is not the product's.
 */
export async function fillAndSubmitDetails(page: Page, guest: Guest): Promise<void> {
  const start = page.locator('input[name="start"]');
  await expect(start).toHaveCount(1);

  await page.locator('input[name="name"]').fill(guest.name);
  await page.locator('input[name="email"]').fill(guest.email);
  if (guest.notes !== undefined) {
    await page.locator('textarea[name="notes"]').fill(guest.notes);
  }

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

  await page.locator('form button[type="submit"]').click();
}

/**
 * Assert the localized confirmation page is on screen: the document is in `locale`, and the
 * `confirmed_heading` for that locale — not the other language's — is the heading rendered.
 */
export async function expectConfirmed(page: Page, locale: BookingLocale): Promise<void> {
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(CONFIRMED_HEADING[locale]);
}

/**
 * Book `slot` from the picker in `locale`, structurally, and assert the LOCALIZED confirmation
 * rendered. The locale-general counterpart of `booking-page.bookSlot`.
 */
export async function bookSlotIn(
  page: Page,
  slot: OfferedSlot,
  guest: Guest,
  locale: BookingLocale,
): Promise<void> {
  await page.locator(`#slots a.slot[href="${slot.href}"]`).click();

  // The hidden `start` must still be the slot we clicked — if the page lost it between steps the
  // booking would land on another time and every later assertion would measure the wrong booking.
  const start = page.locator('input[name="start"]');
  await expect(start).toHaveCount(1);
  expect(new Date(await start.inputValue()).toISOString()).toBe(slot.iso);

  await fillAndSubmitDetails(page, guest);
  await expectConfirmed(page, locale);
}

// --------------------------------------------------------------------------------------
// The confirmation e-mail's `.ics` invite (read off Mailpit, which the `Mail` helper's text view
// does not expose).
// --------------------------------------------------------------------------------------

interface MailpitPart {
  PartID: string;
  FileName: string;
  ContentType: string;
}

interface MailpitFullMessage {
  Attachments?: MailpitPart[];
  Inline?: MailpitPart[];
}

/** RFC 5545 line un-folding: a CRLF/LF followed by a space or tab continues the previous line. */
export function unfoldIcs(ics: string): string {
  return ics.replace(/\r?\n[ \t]/g, "");
}

/**
 * The `text/calendar` invite attached to the message `messageId`, decoded to its raw iCalendar text.
 *
 * @throws if the message carries no calendar attachment — a confirmation e-mail without its `.ics`
 *   is a broken guest journey (RF-08), and a helper that returned `undefined` here would let the
 *   spec sail past exactly that defect.
 */
export async function confirmationInvite(mailpitUrl: string, messageId: string): Promise<string> {
  const base = `${mailpitUrl}/api/v1`;
  const response = await fetch(`${base}/message/${messageId}`);
  if (!response.ok) {
    throw new Error(`Mailpit fetch of ${messageId} failed (${response.status})`);
  }
  const message = (await response.json()) as MailpitFullMessage;
  const parts = [...(message.Attachments ?? []), ...(message.Inline ?? [])];
  const invite = parts.find(
    (part) =>
      part.ContentType.toLowerCase().startsWith("text/calendar") ||
      part.FileName.toLowerCase().endsWith(".ics"),
  );
  if (invite === undefined) {
    throw new Error(
      `the confirmation email ${messageId} carries no .ics calendar attachment (RF-08)`,
    );
  }
  const part = await fetch(`${base}/message/${messageId}/part/${invite.PartID}`);
  if (!part.ok) {
    throw new Error(`Mailpit part fetch of ${messageId}/${invite.PartID} failed (${part.status})`);
  }
  return await part.text();
}
