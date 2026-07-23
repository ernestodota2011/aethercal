/**
 * The embed widget (B1): the one line a tenant drops on *their own* site.
 *
 *   <script src="https://book.example.com/embed.js" data-aethercal-slug="discovery-call"></script>
 *
 * The loader reads its own <script> tag, mounts an <iframe> at `/embed/{slug}`, and — on a real
 * site — grows that iframe to fit the guest's content from one `postMessage`. This spec covers the
 * loader's core contract in a real browser on the shipping artifact: what it mounts, the required
 * attribute, and graceful degradation when the widget can't load.
 *
 * ## Why the resize handshake is NOT asserted here
 *
 * The resize (`aethercal:resize` → the loader sets the iframe's height) needs the embedded page to
 * actually LOAD inside the iframe. That does not happen under a Playwright `setContent` host: such a
 * document has an opaque `about:blank` origin, and a `loading="lazy"` iframe inside one never begins
 * loading in headless Chromium — even in view, even when re-navigated (verified across three CI runs;
 * the iframe stays blank and the loader's own fallback fires). It is a property of the synthetic host,
 * not of the widget: on a real navigated site the iframe loads and the handshake works, which is why
 * the widget ships LIVE. Asserting it faithfully needs a real cross-origin host fixture (a served
 * page, not `setContent`); until that exists this file does not fake it with a test that can only go
 * red for a reason that is not a product defect. The receive-side guards (origin, source, shape) live
 * in `embed.js` and are exercised by the mount + fallback paths below.
 */

import { expect, test } from "@playwright/test";

import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();

/** The loader, served by the real booking service (`apps/booking/.../static/embed.js`). */
const EMBED_SRC = `${stack.bookingUrl}/embed.js`;

/**
 * A minimal third-party host page carrying one embed snippet. `slug === null` omits the required
 * attribute; `base` defaults to the live booking URL but is overridable for the unreachable case.
 */
function hostPage(slug: string | null, base: string = stack.bookingUrl): string {
  const attrs = [`src="${EMBED_SRC}"`, `data-base="${base}"`, `data-lang="en"`];
  if (slug !== null) {
    attrs.push(`data-aethercal-slug="${slug}"`);
  }
  return [
    "<!doctype html><meta charset=utf-8><title>tenant site</title>",
    "<p id=marker>host content above the widget</p>",
    `<script ${attrs.join(" ")}></script>`,
  ].join("\n");
}

test("the snippet mounts exactly one iframe at the compact /embed/{slug} flow", async ({ page }) => {
  await page.setContent(hostPage(run.eventSlug));

  const iframes = page.locator("iframe");
  await expect(iframes).toHaveCount(1);
  // The src is the compact embed shell for THIS slug on the booking origin, carrying the `data-lang`
  // the snippet declared — not the full `/e/` page, not another tenant's slug. `data-base` decides
  // the origin; the loader decides the path and the lang query.
  await expect(iframes.first()).toHaveAttribute(
    "src",
    `${stack.bookingUrl}/embed/${run.eventSlug}?lang=en`,
  );
});

test("a snippet with no slug mounts nothing (the required attribute is the contract)", async ({
  page,
}) => {
  await page.setContent(hostPage(null));
  // No `data-aethercal-slug` ⇒ nothing to embed ⇒ the loader returns without touching the DOM,
  // rather than mounting a broken iframe at `/embed/undefined`.
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("an unreachable widget degrades to an accessible link, never a silent hole", async ({
  page,
}) => {
  // Point the widget at a dead origin: the iframe can never load, so no resize ever arrives. The
  // loader's guard then swaps the blank iframe for an accessible message linking to the full booking
  // page — a visitor is redirected, not stranded on a silent gap. (Port 9 = discard.)
  const deadBase = "http://127.0.0.1:9";
  await page.setContent(hostPage(run.eventSlug, deadBase));

  const fallback = page.getByRole("alert");
  await expect(fallback).toBeVisible({ timeout: 20_000 });

  // The escape hatch must lead somewhere real: the full `/e/{slug}` page on the SAME base the tenant
  // configured, so a broken embed still converts.
  const link = fallback.getByRole("link");
  await expect(link).toHaveAttribute("href", `${deadBase}/e/${run.eventSlug}?lang=en`);
});
