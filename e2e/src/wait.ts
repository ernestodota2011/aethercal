/**
 * Polling with teeth.
 *
 * The stack is asynchronous by design: the confirmation email and the outbound webhook are outbox
 * intents drained by the in-process scheduler on a 60-second interval (`scheduler.py`
 * `DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS` / `DEFAULT_WEBHOOK_INTERVAL_SECONDS`), so an effect that
 * is *going* to happen has not happened yet the instant the browser lands on the confirmation page.
 *
 * `waitFor` therefore polls — and when the deadline passes it THROWS. It never returns `undefined`
 * for the caller to shrug at: an assertion that quietly stops asserting is the silent no-op.
 */

export interface WaitOptions {
  /** Give up (and throw) after this long. */
  timeoutMs?: number;
  /** How often to re-probe. */
  intervalMs?: number;
}

/** The default budget: the scheduler's 60 s tick, plus room for a slow runner. */
export const SCHEDULER_TICK_BUDGET_MS = 100_000;

const sleep = (ms: number): Promise<void> => new Promise((done) => setTimeout(done, ms));

/**
 * Poll `probe` until it returns a value that is neither `undefined` nor `null`, then return it.
 *
 * @throws if the deadline passes first — with the label, the elapsed time, and the last error the
 *   probe raised (a probe that keeps throwing is a symptom, and swallowing it hides the cause).
 */
export async function waitFor<T>(
  label: string,
  probe: () => Promise<T | undefined | null>,
  options: WaitOptions = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? SCHEDULER_TICK_BUDGET_MS;
  const intervalMs = options.intervalMs ?? 1_000;
  const started = Date.now();
  let lastError: unknown;

  while (Date.now() - started < timeoutMs) {
    try {
      const value = await probe();
      if (value !== undefined && value !== null) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(intervalMs);
  }

  const elapsed = ((Date.now() - started) / 1000).toFixed(1);
  const because = lastError instanceof Error ? ` Last error: ${lastError.message}` : "";
  throw new Error(`Timed out after ${elapsed}s waiting for: ${label}.${because}`);
}
