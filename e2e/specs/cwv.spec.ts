/**
 * The Core Web Vitals guard (U-01, gap G-9): the booking page's LCP and CLS, budgeted, in CI.
 *
 * The booking page was measured once at LCP ≈ 367 ms / CLS 0 (2026-07-18). A single measurement is a
 * photograph — a regression (a render-blocking script, a heavy hero, a late layout shift) would slip
 * through in silence, because nothing was watching after that day. This spec is the guard: it drives
 * the SAME public booking event page every other browser spec uses, on the SAME shipping stack, and
 * fails the build if LCP or CLS crosses its budget (`src/cwv.ts`).
 *
 * It complements — does not duplicate — the STATIC bundle-byte ceiling in
 * `packages/aethercal-ui/tests/test_release_hardening.py`: that guard weighs the committed asset;
 * this one measures what the guest actually experiences at runtime, which bytes alone cannot predict.
 *
 * ## The measured page
 *
 * By default: the booking event page on the shipping stack, resolved from the run fixtures the way
 * `global-setup.ts` proves it is served. Set `E2E_CWV_URL` to point the guard at any deployed booking
 * page instead (staging, live `book.aetherlogik.com`, or a local fixture) — see `cwvTargetUrl`. In
 * that mode the guard needs no running stack, so it does not read `.stack.json`/`.run.json`.
 */

import { expect, test } from "@playwright/test";

import { CLS_BUDGET, LCP_BUDGET_MS, cwvTargetUrl, measureWebVitals } from "../src/cwv.js";
import { runContext, stackConfig } from "../src/stack.js";

/**
 * The booking event page on the shipping stack: the exact URL `global-setup.ts` asserts is served
 * (`/e/{slug}?tz=UTC&lang=en`). Resolved lazily so an `E2E_CWV_URL` run never touches the stack
 * fixtures (which would throw when there is no stack — the whole point of the override).
 */
function shippingStackBookingUrl(): string {
  const stack = stackConfig();
  const run = runContext();
  return `${stack.bookingUrl}/e/${run.eventSlug}?tz=UTC&lang=en`;
}

test("the booking page stays within its Core Web Vitals budget (LCP + CLS)", async ({ page }) => {
  const url = cwvTargetUrl(shippingStackBookingUrl);

  const { lcp, cls, lcpSeen } = await measureWebVitals(page, url);

  // Reported unconditionally — to stdout (the CI log) and the HTML report — so a passing run still
  // leaves the numbers behind: a budget with headroom to spare and one about to be breached must not
  // look identical in a green build.
  const summary = `${url} — LCP ${lcp.toFixed(0)} ms (budget ${LCP_BUDGET_MS}), CLS ${cls.toFixed(3)} (budget ${CLS_BUDGET})`;
  process.stdout.write(`[cwv] ${summary}\n`);
  test.info().annotations.push({ type: "core-web-vitals", description: summary });

  // Fail CLOSED: no LCP entry means the page rendered nothing contentful (a blank page, a broken
  // stack), and `lcp` is a meaningless 0 that would sail under any budget. A guard that passes on a
  // page that never rendered is the silent no-op — so the ABSENCE of a measurement is itself a fail.
  expect(
    lcpSeen,
    `No LCP entry was observed for ${url} — the page rendered no contentful element, so there is ` +
      "nothing to budget. This is a broken page, not a fast one; failing rather than passing on zero.",
  ).toBe(true);

  expect(
    lcp,
    `LCP ${lcp.toFixed(0)} ms exceeds the ${LCP_BUDGET_MS} ms budget for ${url} — the largest ` +
      "element now renders too late (a render-blocking script, a heavy/slow hero, or a redirect).",
  ).toBeLessThanOrEqual(LCP_BUDGET_MS);

  expect(
    cls,
    `CLS ${cls.toFixed(3)} exceeds the ${CLS_BUDGET} budget for ${url} — content is shifting under ` +
      "the guest (an image/embed without reserved space, or a late-injected banner).",
  ).toBeLessThanOrEqual(CLS_BUDGET);
});
