/**
 * Core Web Vitals measurement + budgets — the runtime half of the performance guard (U-01, G-9).
 *
 * The committed JS bundle already has a STATIC byte ceiling (`test_release_hardening.py`, the
 * "90 KB"/now-100 KB budget). That guard cannot see a RUNTIME regression: a render-blocking script,
 * a heavy hero image, or a late layout shift can wreck the guest's experience without adding a
 * single byte to the calendar bundle. This module is what closes that gap — it measures the two
 * field metrics the booking page lives or dies by (LCP, CLS) in a real Chromium against the SHIPPING
 * booking page, so a regression fails CI instead of passing in silence.
 *
 * The booking page was measured at LCP ≈ 367 ms / CLS 0 on 2026-07-18 — but that was a photograph,
 * not a gate. These budgets are the guard the photograph never was.
 */

import { type Page } from "@playwright/test";

/**
 * The Largest Contentful Paint ceiling, in milliseconds — Google's "good" LCP threshold.
 *
 * Chosen at the "good" line (not tighter) on purpose: the CI runner is far slower and noisier than
 * the live CT129 host, so a budget hugging the 367 ms photograph would false-positive on runner
 * jitter, and a guard that cries wolf gets disabled. 2500 ms still has ample teeth — a render-
 * blocking asset or a heavy/slow hero pushes a page like this well past 3 s, which is exactly the
 * regression this guard exists to catch (see the control in the PR: a slow hero image → ~3.5 s → red).
 */
export const LCP_BUDGET_MS = 2500;

/** The Cumulative Layout Shift ceiling — Google's "good" CLS threshold. The page measured 0. */
export const CLS_BUDGET = 0.1;

/** How long (ms) to keep observing after `load` so a late LCP candidate / layout shift is counted. */
const SETTLE_MS = 1000;

export interface WebVitals {
  /** Largest Contentful Paint, in milliseconds. */
  lcp: number;
  /** Cumulative Layout Shift — the official session-window metric (unitless). */
  cls: number;
  /**
   * Whether ANY largest-contentful-paint entry was observed. `false` means the page rendered no
   * contentful element at all (a blank page, a failed navigation) — in which case `lcp` is a
   * meaningless 0 and the caller must fail closed, never pass the budget by measuring nothing.
   */
  lcpSeen: boolean;
}

/**
 * The URL whose Core Web Vitals the guard measures.
 *
 * By default it is the booking EVENT page on the shipping stack (`fallback()`), the same page every
 * other browser spec drives and the one `global-setup.ts` proves is served. `E2E_CWV_URL` overrides
 * it, so the very same guard can be pointed at ANY deployed booking page — a staging URL, the live
 * `book.aetherlogik.com`, or (in the PR's control) a local fixture on a private port — without a
 * running stack. That override is a real capability (continuous CWV against production, G-9), not a
 * test-only seam.
 */
export function cwvTargetUrl(fallback: () => string): string {
  const override = process.env.E2E_CWV_URL;
  return override !== undefined && override !== "" ? override : fallback();
}

interface LayoutShiftEntry extends PerformanceEntry {
  value: number;
  hadRecentInput: boolean;
}

/** A layout-shift session ends after this gap between shifts, or once it has spanned MAX. */
const CLS_SESSION_GAP_MS = 1000;
const CLS_SESSION_MAX_MS = 5000;

/**
 * Load `url` in `page` and report its lab Core Web Vitals.
 *
 * Fails closed on a non-2xx (or non-document) navigation: measuring an error page and calling its
 * empty vitals "within budget" is the silent no-op this guard exists to prevent.
 *
 * Waits for the `load` event (so a slow LCP resource is already painted — the browser's `load`
 * blocks on it), then keeps the buffered PerformanceObservers alive for `SETTLE_MS` more so a
 * late-arriving LCP candidate or a post-load layout shift is still counted. `buffered: true` replays
 * the entries that fired during navigation, so nothing before the observer is created is lost — and
 * `takeRecords()` at the end drains anything buffered but not yet delivered to a callback, whose
 * return value MUST be processed (dropping it would silently lose a last-moment LCP or shift).
 *
 * CLS is the OFFICIAL Core Web Vitals metric: the largest "session window" of layout shifts, where a
 * window ends after a 1 s gap or once it spans 5 s — not a naive lifetime sum (which over-reports and
 * can false-fail a page the field metric would pass).
 */
export async function measureWebVitals(page: Page, url: string): Promise<WebVitals> {
  const response = await page.goto(url, { waitUntil: "load" });
  if (response === null) {
    throw new Error(`Navigating to ${url} produced no response (not a document navigation).`);
  }
  if (!response.ok()) {
    throw new Error(
      `The page at ${url} answered HTTP ${response.status()} — refusing to measure a non-2xx page ` +
        "(a friendly error page has excellent, and meaningless, Core Web Vitals).",
    );
  }
  return page.evaluate(
    ([settleMs, sessionGapMs, sessionMaxMs]) =>
      new Promise<WebVitals>((resolve) => {
        let lcp = 0;
        let lcpSeen = false;
        // LCP: the browser reports a new entry every time the largest element grows; the last one
        // wins. `startTime` is the render time (or load time) of that element.
        const recordLcp = (entries: PerformanceEntryList): void => {
          for (const entry of entries) {
            lcpSeen = true;
            if (entry.startTime > lcp) lcp = entry.startTime;
          }
        };
        // CLS session windows: accumulate shifts (excluding those after recent input) into a window,
        // opening a fresh one on a >gap idle or once the window has spanned >max; the reported CLS is
        // the largest window ever seen.
        let cls = 0;
        let windowValue = 0;
        let windowStart = 0;
        let windowLast = 0;
        const recordCls = (entries: PerformanceEntryList): void => {
          for (const entry of entries) {
            const shift = entry as LayoutShiftEntry;
            if (shift.hadRecentInput) continue;
            if (
              windowValue !== 0 &&
              (shift.startTime - windowLast > sessionGapMs ||
                shift.startTime - windowStart > sessionMaxMs)
            ) {
              windowValue = 0;
            }
            if (windowValue === 0) windowStart = shift.startTime;
            windowLast = shift.startTime;
            windowValue += shift.value;
            if (windowValue > cls) cls = windowValue;
          }
        };
        const lcpObserver = new PerformanceObserver((list) => {
          recordLcp(list.getEntries());
        });
        lcpObserver.observe({ type: "largest-contentful-paint", buffered: true });
        const clsObserver = new PerformanceObserver((list) => {
          recordCls(list.getEntries());
        });
        clsObserver.observe({ type: "layout-shift", buffered: true });
        setTimeout(() => {
          // Drain entries buffered but not yet delivered to the callbacks, then finalize.
          recordLcp(lcpObserver.takeRecords());
          recordCls(clsObserver.takeRecords());
          lcpObserver.disconnect();
          clsObserver.disconnect();
          resolve({ lcp, cls, lcpSeen });
        }, settleMs);
      }),
    [SETTLE_MS, CLS_SESSION_GAP_MS, CLS_SESSION_MAX_MS] as const,
  );
}
