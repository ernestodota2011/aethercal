/**
 * The embed widget (B1): the one line a tenant drops on *their own* site.
 *
 *   <script src="https://book.example.com/embed.js" data-aethercal-slug="discovery-call"></script>
 *
 * Everything the loader does — read its own <script> tag, mount a cross-origin <iframe> at
 * `/embed/{slug}`, and grow that iframe to fit the guest's content by trusting one resize message —
 * lives in the *seam between two origins*: the host page and the booking service. A jsdom unit test
 * can drive the code but fakes the very things that carry the disagreement (a real cross-origin
 * `postMessage`, a real iframe that either loads or does not). So this is a browser spec, on the
 * shipping artifact, exactly like the golden flow.
 *
 * The host is a `setContent` page carrying the one-line snippet. One wrinkle it forces us to handle:
 * a `loading="lazy"` iframe inside such a document does not begin loading on its own in headless
 * Chromium, even when it is in view. That is the browser's lazy heuristic, not the widget — the mount,
 * the src, the no-slug contract and the unreachable-fallback are all exercised without a load, and the
 * one test that genuinely needs the iframe's content (the resize handshake) forces the load itself.
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

test("the widget grows to fit its content via the cross-origin resize handshake", async ({
  page,
}) => {
  await page.setContent(hostPage(run.eventSlug));
  const iframe = page.locator("iframe").first();
  await expect(iframe).toHaveCount(1);

  // Force the mounted-but-lazy iframe to load (see file header): re-navigating through about:blank
  // guarantees a fresh load regardless of the lazy heuristic. Once it loads, the embedded page posts
  // `{type:'aethercal:resize', height:<scrollHeight>}` to its parent (views.py `EMBED_RESIZE_SCRIPT`,
  // allowed by a CSP sha256 hash) and the loader answers by setting the iframe's inline height. A
  // concrete `Npx` here is proof the message crossed the origin boundary, passed the
  // origin+source+shape guards, and was applied — the entire reason the widget is not a fixed box.
  await iframe.evaluate((el) => {
    const frame = el as HTMLIFrameElement;
    frame.loading = "eager";
    const src = frame.src;
    frame.src = "about:blank";
    frame.src = src;
  });

  await expect
    .poll(async () => iframe.evaluate((el) => (el as HTMLIFrameElement).style.height), {
      message:
        "the iframe never received a valid aethercal:resize — the embed page did not post one, " +
        "or the loader's origin/source/shape guard rejected it",
      timeout: 20_000,
    })
    .toMatch(/^\d+px$/);
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
