/**
 * The guest's mailbox (Mailpit), and the links the product mails them.
 *
 * The confirmation email is not decoration: it is the ONLY place a guest ever receives their
 * cancel/reschedule links (the confirmation *page* does not render them — `views.confirmation_page`
 * shows when/where/meeting-link and nothing else). So "cancel with the signed guest link" is only
 * an end-to-end truth if the link comes out of a real message that a real SMTP send delivered.
 */

import type { StackConfig } from "./stack.js";
import { waitFor } from "./wait.js";

interface MailpitSummary {
  ID: string;
  Subject: string;
  To: { Address: string }[];
}

interface MailpitList {
  messages: MailpitSummary[];
}

interface MailpitMessage {
  ID: string;
  Subject: string;
  Text: string;
  HTML: string;
}

export interface GuestLinks {
  /** The URL the product mailed for "cancel" — verbatim, not reconstructed. */
  cancel: string;
  /** The URL the product mailed for "reschedule" — verbatim, not reconstructed. */
  reschedule: string;
}

export interface DeliveredMail {
  id: string;
  subject: string;
  text: string;
}

const CANCEL_LINK = /https?:\/\/[^\s<>"]+\/cancel\?[^\s<>"]+/;
const RESCHEDULE_LINK = /https?:\/\/[^\s<>"]+\/reschedule\?[^\s<>"]+/;

export class Mail {
  private readonly base: string;

  constructor(stack: StackConfig) {
    this.base = `${stack.mailpitUrl}/api/v1`;
  }

  /** Reachability probe for global setup — a mailbox we cannot read is a broken run, not a skip. */
  async assertReachable(): Promise<void> {
    const response = await fetch(`${this.base}/messages?limit=1`);
    if (!response.ok) {
      throw new Error(`Mailpit is not reachable (${response.status}) at ${this.base}`);
    }
  }

  /** Empty the mailbox so a run never reads a previous run's message. */
  async purge(): Promise<void> {
    const response = await fetch(`${this.base}/messages`, { method: "DELETE" });
    if (!response.ok) {
      throw new Error(`Could not purge Mailpit (${response.status})`);
    }
  }

  private async list(): Promise<MailpitSummary[]> {
    const response = await fetch(`${this.base}/messages?limit=200`);
    if (!response.ok) {
      throw new Error(`Mailpit list failed (${response.status})`);
    }
    return ((await response.json()) as MailpitList).messages;
  }

  private async message(id: string): Promise<MailpitMessage> {
    const response = await fetch(`${this.base}/message/${id}`);
    if (!response.ok) {
      throw new Error(`Mailpit fetch of ${id} failed (${response.status})`);
    }
    return (await response.json()) as MailpitMessage;
  }

  /**
   * Wait for a message addressed to `recipient` whose subject contains `subjectContains`,
   * ignoring any message id in `seen` (so the reschedule mail is never confused with the
   * confirmation mail that preceded it).
   *
   * @throws when no such message lands inside the budget.
   */
  async waitForMessage(
    recipient: string,
    subjectContains: string,
    seen: ReadonlySet<string> = new Set(),
  ): Promise<DeliveredMail> {
    const summary = await waitFor(
      `an email to ${recipient} whose subject contains ${JSON.stringify(subjectContains)}`,
      async () => {
        const messages = await this.list();
        return messages.find(
          (candidate) =>
            !seen.has(candidate.ID) &&
            candidate.To.some((to) => to.Address.toLowerCase() === recipient.toLowerCase()) &&
            candidate.Subject.includes(subjectContains),
        );
      },
    );
    const full = await this.message(summary.ID);
    return { id: full.ID, subject: full.Subject, text: full.Text };
  }
}

/**
 * The cancel + reschedule links exactly as the guest received them.
 *
 * @throws if either link is absent — an email that does not carry them is a broken guest journey,
 *   and a helper that returned `undefined` here would let the spec sail past the defect.
 */
export function guestLinks(mail: DeliveredMail): GuestLinks {
  const cancel = CANCEL_LINK.exec(mail.text)?.[0];
  const reschedule = RESCHEDULE_LINK.exec(mail.text)?.[0];
  if (cancel === undefined || reschedule === undefined) {
    throw new Error(
      [
        `The email ${JSON.stringify(mail.subject)} carries no cancel/reschedule link.`,
        "---",
        mail.text,
        "---",
      ].join("\n"),
    );
  }
  return { cancel, reschedule };
}
