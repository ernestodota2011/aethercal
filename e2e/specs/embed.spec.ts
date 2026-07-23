/**
 * The embed widget (B1): the one line a tenant drops on *their own* site.
 *
 *   <script src="https://book.example.com/embed.js" data-aethercal-slug="discovery-call"></script>
 *
 * Everything the loader does — read its own <script> tag, mount a cross-origin <iframe> at
 * `/embed/{slug}`, and grow that iframe to fit the guest's content by trusting one resize message —
 * lives in the *seam between two origins*: the host page and the booking service. A jsdom unit test
 * can drive the code but fakes the very things that carry the disagreement (a real cross-origin
 * `postMessage`, a real IntersectionObserver, an iframe that either loads or does not). So this is a
 * browser spec, on the shipping artifact, exactly like the golden flow.
 *
 * The host page is served from a REAL, distinct origin (`tenant.embedder.test`, fulfilled by
 * `page.route`), NOT `about:blank`. That matters: `setContent` gives an opaque-origin document, and a
 * lazy cross-origin iframe inside one never triggers its load — the widget would fall back to its
 * "couldn't load" link and the resize handshake would never happen. A real navigated origin is both
 * faithful (it is the relationship a customer's WordPress site has to `book.aetherlogik.com`) and the
 * only place the load + handshake actually fire.
 */

import { expect, type Page, test } from "@playwright/test";

import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();

/** The loader, served by the real booking service (`apps/booking/.../static/embed.js`). */
const EMBED_SRC = `${stack.bookingUrl}/embed.js`;

/** A real third-party host origin, distinct from the booking stack, served entirely by `route`. */
const HOST_URL = "http://tenant.embedder.test/";

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

/** Serve `html` as a real navigated document at `HOST_URL` (cross-origin to the booking stack). */
async function openHost(page: Page, html: string): Promise<void> {
  await page.route(HOST_URL, (route) =>
    route.fulfill({ contentType: "text/html; charset=utf-8", body: html }),
  );
  await page.goto(HOST_URL);
}

test("the snippet mounts exactly one iframe at the compact /embed/{slug} flow", async ({ page }) => {
  await openHost(page, hostPage(run.eventSlug));

  const iframes = page.locator("iframe");
  await expect(iframes).toHaveCount(1);
  // The src is the compact embed shell for THIS slug on the booking origin, carrying the `data-lang`
  // the snippet declared — not the full `/e/` page, not another tenant's slug. `data-base` decides
  // the origin; the loader decides the path and the lang query. If any drifts, the tenant embeds the
  // wrong thing and never knows.
  await expect(iframes.first()).toHaveAttribute(
    "src",
    `${stack.bookingUrl}/embed/${run.eventSlug}?lang=en`,
  );
});

test("the widget grows to fit its content via the cross-origin resize handshake", async ({
  page,
}) => {
  await openHost(page, hostPage(run.eventSlug));

  // Bring the lazy iframe into view so it loads (it is at the top of the page, but make the load a
  // guarantee, not a layout accident). The embedded page then posts `{type:'aethercal:resize',
  // height:<scrollHeight>}` to its parent (views.py `EMBED_RESIZE_SCRIPT`, allowed by a CSP sha256
  // hash), and the loader answers by setting the iframe's inline height to that many pixels. A
  // concrete `Npx` here is proof the message crossed the origin boundary, passed the
  // origin+source+shape guards, and was applied — the entire reason the widget is not a fixed box.
  const iframe = page.locator("iframe").first();
  await iframe.scrollIntoViewIfNeeded();
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
  await openHost(page, hostPage(null));
  // No `data-aethercal-slug` ⇒ nothing to embed ⇒ the loader returns without touching the DOM,
  // rather than mounting a broken iframe at `/embed/undefined`.
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("an unreachable widget degrades to an accessible link, never a silent hole", async ({
  page,
}) => {
  // Point the widget at a dead origin: the iframe can never load, so no resize ever arrives. The
  // loader's 12s guard then swaps the blank iframe for an accessible message linking to the full
  // booking page — a visitor is redirected, not stranded on a silent gap. (Port 9 = discard.)
  const deadBase = "http://127.0.0.1:9";
  await openHost(page, hostPage(run.eventSlug, deadBase));

  const fallback = page.getByRole("alert");
  await expect(fallback).toBeVisible({ timeout: 20_000 });

  // The escape hatch must lead somewhere real: the full `/e/{slug}` page on the SAME base the tenant
  // configured, so a broken embed still converts.
  const link = fallback.getByRole("link");
  await expect(link).toHaveAttribute("href", `${deadBase}/e/${run.eventSlug}?lang=en`);
});
