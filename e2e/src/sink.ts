/**
 * The webhook sink: what the server actually POSTed, byte for byte.
 *
 * The sink stores the RAW request body (base64) and the RAW headers. That matters: the signature is
 * an HMAC over the exact bytes on the wire (`webhooks/signing.py` — canonical JSON, sorted keys,
 * compact separators), so a test that re-serialised the parsed JSON and signed *that* would be
 * verifying its own arithmetic, not the server's. We verify the bytes we received.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

import type { StackConfig } from "./stack.js";
import { waitFor } from "./wait.js";

/** The header the delivery worker signs with (`webhooks/signing.py::SIGNATURE_HEADER`). */
export const SIGNATURE_HEADER = "x-aethercal-signature";

export interface CapturedDelivery {
  receivedAt: string;
  headers: Record<string, string>;
  /** The exact bytes the server sent. */
  body: Buffer;
}

interface RawCapture {
  received_at: string;
  headers: Record<string, string>;
  body_b64: string;
}

export interface WebhookEnvelope {
  event: string;
  api_version: string;
  timestamp: string;
  data: Record<string, unknown> & { id?: string; status?: string };
}

/**
 * True iff `signature` is the HMAC-SHA256 of `body` under `secret` (constant time).
 *
 * Accepts the header's `sha256=<hex>` form. The delivery worker keys the HMAC with the UTF-8 bytes
 * of the subscriber secret (`services/webhooks.py::create_webhook` stores `secret.encode("utf-8")`).
 */
export function verifySignature(body: Buffer, secret: string, signature: string): boolean {
  const presented = signature.startsWith("sha256=") ? signature.slice("sha256=".length) : signature;
  const expected = createHmac("sha256", Buffer.from(secret, "utf8")).update(body).digest("hex");
  const a = Buffer.from(presented, "utf8");
  const b = Buffer.from(expected, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

export class Sink {
  private readonly base: string;

  constructor(stack: StackConfig) {
    this.base = stack.sinkUrl;
  }

  /** Reachability probe for global setup — an unreachable sink fails the run, never skips it. */
  async assertReachable(): Promise<void> {
    const response = await fetch(`${this.base}/_health`);
    if (!response.ok) {
      throw new Error(`The webhook sink is not reachable (${response.status}) at ${this.base}`);
    }
  }

  /** Drop everything captured so far, so one run never reads another's deliveries. */
  async reset(): Promise<void> {
    const response = await fetch(`${this.base}/_captured`, { method: "DELETE" });
    if (!response.ok) {
      throw new Error(`Could not reset the webhook sink (${response.status})`);
    }
  }

  async captured(): Promise<CapturedDelivery[]> {
    const response = await fetch(`${this.base}/_captured`);
    if (!response.ok) {
      throw new Error(`The webhook sink refused to list deliveries (${response.status})`);
    }
    const raw = (await response.json()) as { captured: RawCapture[] };
    return raw.captured.map((entry) => ({
      receivedAt: entry.received_at,
      headers: entry.headers,
      body: Buffer.from(entry.body_b64, "base64"),
    }));
  }

  /**
   * Wait for the delivery of `event` for booking `bookingId`.
   *
   * @throws if it never arrives inside the budget — the scheduler's webhook tick is 60 s, so this
   *   waits, but it does not forgive.
   */
  async waitForDelivery(event: string, bookingId: string): Promise<CapturedDelivery> {
    return waitFor(`the ${event} webhook for booking ${bookingId}`, async () => {
      const deliveries = await this.captured();
      return deliveries.find((delivery) => {
        const envelope = parseEnvelope(delivery);
        return envelope.event === event && envelope.data.id === bookingId;
      });
    });
  }
}

/** The delivery's JSON envelope. Throws on malformed JSON — a body we cannot parse is a failure. */
export function parseEnvelope(delivery: CapturedDelivery): WebhookEnvelope {
  return JSON.parse(delivery.body.toString("utf8")) as WebhookEnvelope;
}

/** The signature header of a delivery, or `undefined` when the server sent none (a defect). */
export function signatureOf(delivery: CapturedDelivery): string | undefined {
  return delivery.headers[SIGNATURE_HEADER];
}
