/**
 * Where the stack under test lives — and the refusal to run without one.
 *
 * The suite NEVER boots the application itself: it points at a stack someone else brought up
 * (`scripts/stack-up.sh`, which runs the SHIPPING artifact — `deploy/docker-compose.yml` — so the
 * thing under test is the thing users get). Configuration arrives either as `E2E_*` environment
 * variables or in `.stack.json`, which `stack-up.sh` writes.
 *
 * The one rule this module exists to enforce: **a missing stack is a hard error, never a skip.**
 * A suite that silently passes because it had nothing to talk to is the silent no-op applied to
 * our own safety net — the exact failure mode the design doc names as this project's disease.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/** `e2e/` — the root of this package, whatever the CWD of the runner is. */
export const E2E_ROOT = resolve(HERE, "..");

/** Written by `scripts/stack-up.sh` after the stack is healthy and bootstrapped. */
export const STACK_FILE = resolve(E2E_ROOT, ".stack.json");

/** Written by `global-setup.ts` — the per-run fixtures the specs share. */
export const RUN_FILE = resolve(E2E_ROOT, ".run.json");

export interface StackConfig {
  /** The API, as reachable from the test runner. */
  apiUrl: string;
  /** The public booking page, as reachable from the test runner. */
  bookingUrl: string;
  /** Mailpit's HTTP API — the mailbox the app's SMTP lands in. */
  mailpitUrl: string;
  /** The webhook sink's inspection API, as reachable from the test runner. */
  sinkUrl: string;
  /** The webhook sink as reachable FROM THE SERVER (container-side name) — what we subscribe. */
  sinkWebhookUrl: string;
  /** An API key for the bootstrapped tenant. */
  apiKey: string;
  tenantId: string;
  /** The tenant's first user — the host every event type hangs off. */
  hostUserId: string;
}

const KEYS = {
  apiUrl: "E2E_API_URL",
  bookingUrl: "E2E_BOOKING_URL",
  mailpitUrl: "E2E_MAILPIT_URL",
  sinkUrl: "E2E_SINK_URL",
  sinkWebhookUrl: "E2E_SINK_WEBHOOK_URL",
  apiKey: "E2E_API_KEY",
  tenantId: "E2E_TENANT_ID",
  hostUserId: "E2E_HOST_USER_ID",
} as const satisfies Record<keyof StackConfig, string>;

function readStackFile(): Partial<Record<keyof StackConfig, string>> {
  try {
    const raw: unknown = JSON.parse(readFileSync(STACK_FILE, "utf8"));
    return typeof raw === "object" && raw !== null
      ? (raw as Partial<Record<keyof StackConfig, string>>)
      : {};
  } catch {
    return {};
  }
}

/**
 * The stack under test. Environment wins over `.stack.json`; anything still missing is fatal.
 *
 * @throws if any value is absent — the suite must not run against a phantom stack.
 */
export function stackConfig(): StackConfig {
  const file = readStackFile();
  const resolved: Partial<Record<keyof StackConfig, string>> = {};
  const missing: string[] = [];

  for (const [field, envName] of Object.entries(KEYS) as [keyof StackConfig, string][]) {
    const value = process.env[envName] ?? file[field];
    if (value === undefined || value === "") {
      missing.push(envName);
      continue;
    }
    resolved[field] = value.replace(/\/+$/, "");
  }

  if (missing.length > 0) {
    throw new Error(
      [
        `The E2E suite has no stack to talk to — missing: ${missing.join(", ")}.`,
        `Bring the stack up first (it writes ${STACK_FILE}):`,
        "    pnpm --dir e2e stack:up",
        "This is a hard failure on purpose: a suite that skips itself when the stack is absent",
        "reports green without having tested anything.",
      ].join("\n"),
    );
  }
  return resolved as StackConfig;
}

export interface RunContext {
  /** Unique per `playwright test` invocation — namespaces slugs and guest emails. */
  runId: string;
  scheduleId: string;
  eventTypeId: string;
  eventSlug: string;
  eventTitle: string;
  /** Minutes the event lasts — used to compute the expected slot grid. */
  durationMinutes: number;
  webhookId: string;
  /** The plaintext HMAC key, returned exactly once when the subscription is created. */
  webhookSecret: string;
}

/** The fixtures `global-setup.ts` created for this run. Absent file ⇒ hard error (never a skip). */
export function runContext(): RunContext {
  let raw: string;
  try {
    raw = readFileSync(RUN_FILE, "utf8");
  } catch {
    throw new Error(
      `${RUN_FILE} is missing — global setup did not run (or failed). The specs cannot invent it.`,
    );
  }
  return JSON.parse(raw) as RunContext;
}
