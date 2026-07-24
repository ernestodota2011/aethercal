/**
 * Playwright configuration for the AetherCal end-to-end suite.
 *
 * Deliberate choices:
 *
 * * **No `webServer`.** The suite never boots the app itself — it runs against the SHIPPING
 *   artifact (`deploy/docker-compose.yml`, brought up by `scripts/stack-up.sh`). Testing a
 *   dev-server nobody deploys would prove the wrong thing.
 * * **`workers: 1`, no parallelism.** Every spec competes for the same finite slot grid of the same
 *   event type; parallel workers would race for a slot and the loser would look like a product bug.
 * * **`retries: 0`.** A retry turns a real, intermittent defect into a green report. If a run is
 *   flaky, that is a finding — not noise to be smoothed over.
 * * **Long timeouts.** The confirmation email and the outbound webhook are outbox intents drained by
 *   the scheduler's 60-second tick, so the golden journey legitimately spends minutes waiting.
 */

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  globalSetup: "./global-setup.ts",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["junit", { outputFile: "test-results/junit.xml" }],
  ],
  use: {
    // Artifacts on failure only: a passing run stays cheap, a failing one is fully reconstructable.
    trace: "retain-on-failure",
    video: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    // The stack is plain HTTP on localhost; nothing here should ever ignore a bad certificate.
    ignoreHTTPSErrors: false,
  },
  projects: [
    {
      name: "golden",
      // The golden journey only. `a11y`, `embed`, `cwv` and `booking` are their own projects/jobs,
      // so exclude each — a bare `testIgnore: /a11y/` would silently sweep every new sibling spec
      // into golden.
      testIgnore: /(a11y|embed|cwv|booking)\.spec\.ts/,
      // Three lifecycle events × (a ≤60 s outbox tick + a ≤60 s webhook tick), plus the browser.
      timeout: 8 * 60_000,
      use: { browserName: "chromium" },
    },
    {
      name: "a11y",
      testMatch: /a11y\.spec\.ts/,
      timeout: 90_000,
      use: { browserName: "chromium" },
    },
    {
      name: "embed",
      testMatch: /embed\.spec\.ts/,
      // The resize handshake and the 12 s unreachable-widget fallback are the slow assertions; a
      // full journey's minutes are not needed here.
      timeout: 90_000,
      use: { browserName: "chromium" },
    },
    {
      name: "cwv",
      // The Core Web Vitals guard (U-01/G-9): budgets the booking page's LCP + CLS. One navigation
      // plus a short settle window — no outbox ticks to wait on.
      testMatch: /cwv\.spec\.ts/,
      timeout: 90_000,
      use: { browserName: "chromium" },
    },
    {
      name: "booking",
      testMatch: /booking\.spec\.ts/,
      // F-01.1 waits for the confirmation email — an outbox intent drained by the scheduler's ≤60 s
      // tick — so this leg needs more than the a11y/embed 90 s, but far less than the golden
      // journey's eight minutes (it drains one email, not three lifecycle events × two ticks).
      timeout: 4 * 60_000,
      use: { browserName: "chromium" },
    },
  ],
});
