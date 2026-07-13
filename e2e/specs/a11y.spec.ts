/**
 * Accessibility (RNF-7): axe-core over the public booking page, on every step of the ≤3-step flow,
 * in both shipped locales.
 *
 * This exists because the axe run that caught the contrast bug was **manual and not repeatable** —
 * a one-off audit is a snapshot, not a gate, and the next regression lands unseen. Here it is a job.
 *
 * The assertion is zero WCAG 2.0/2.1 A+AA violations. Not "few", not "no new ones": a baseline of
 * accepted violations is how a suite learns to shrug.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { Api } from "../src/api.js";
import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();
const api = new Api(stack);

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const LOCALES = ["en", "es"] as const;

interface Violation {
  id: string;
  impact?: string | null | undefined;
  help: string;
  nodes: { target: unknown[] }[];
}

function summarise(violations: Violation[]): string {
  return violations
    .map((violation) => {
      const targets = violation.nodes
        .map((node) => JSON.stringify(node.target))
        .slice(0, 5)
        .join(", ");
      return `  • [${violation.impact ?? "n/a"}] ${violation.id} — ${violation.help}\n    ${targets}`;
    })
    .join("\n");
}

async function auditPage(page: Page, url: string): Promise<void> {
  await page.goto(url);
  const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
  const violations = results.violations as unknown as Violation[];
  expect(
    violations,
    `axe found ${violations.length} WCAG A/AA violation(s) on ${url}:\n${summarise(violations)}`,
  ).toEqual([]);
}

/** A start the page will actually render a details form for (step 2 needs a real slot). */
async function firstOfferedStart(): Promise<string> {
  const offered = await api.offeredStarts(run.eventTypeId);
  const start = offered[0];
  if (start === undefined) {
    throw new Error("no slot on offer — the booking form (step 2) cannot be audited");
  }
  return start;
}

for (const lang of LOCALES) {
  test(`the event list is accessible (${lang})`, async ({ page }) => {
    await auditPage(page, `${stack.bookingUrl}/?lang=${lang}`);
  });

  test(`the event page and its slot picker are accessible (${lang})`, async ({ page }) => {
    await auditPage(page, `${stack.bookingUrl}/e/${run.eventSlug}?tz=UTC&lang=${lang}`);
  });

  test(`the booking details form is accessible (${lang})`, async ({ page }) => {
    const start = await firstOfferedStart();
    const query = new URLSearchParams({ start, tz: "UTC", lang });
    await auditPage(page, `${stack.bookingUrl}/e/${run.eventSlug}/book?${query.toString()}`);
  });
}
