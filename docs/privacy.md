# Privacy and data retention

This document describes **what personal data AetherCal handles, why, how long it is kept, and how a
data-subject erasure request is fulfilled.** It is written for two readers: an operator running an
instance (who is the data *controller* and can adapt this into their own published policy), and a
guest who wants to know what happens to what they type into a booking page.

> AetherCal is the *software*. When you self-host it, **you are the data controller** for the guests
> who book with you: this document tells you what the software does so you can meet your own legal
> obligations, but it is not itself legal advice or a compliance certification.

## What is collected, and why

A guest provides only what a booking needs. Every field below is collected for the single purpose of
scheduling, confirming, and (if configured) reminding them about the appointment they asked for.

| Data | Where it is stored | Purpose |
|---|---|---|
| Name, email | `bookings` | Identify the guest; send the confirmation and reminders |
| Phone + consent timestamp | `bookings` | Only if the event asks for it and the guest ticks consent (see [phone-channels](phone-channels.md)) |
| Notes, answers to the event's questions | `bookings` | Context the host asked for |
| Timezone | `bookings` | Render times correctly and schedule the event |
| Source IP | `bookings.source_ip` | Rate-limiting and abuse defense on a public form |
| Email (again) in queued messages | `outbox`, `sent_notifications` | Deliver the notification; the ledger records that it was sent |
| The whole booking, serialized | `webhook_deliveries` | Only if the operator configured an outbound webhook |
| Guest access tokens | `guest_tokens` | The unauthenticated cancel/reschedule links in the guest's email |

No tracking cookies, no analytics beacons, no third-party scripts: the booking page self-hosts its
only script dependency and runs under a strict `script-src 'self'` Content-Security-Policy.

## Credentials are encrypted at rest

An operator connects their own payment/calendar/messaging accounts ([BYOK](byok-credentials.md)).
Those credentials are encrypted with Fernet (AES-128-CBC + HMAC) using a key derived from
`AETHERCAL_APP_SECRET`; the ciphertext is what lives in the database, never the raw secret.

## Retention and erasure

AetherCal does **not** delete bookings on a timer. The appointment happened: it occupied that
half-hour, it is in the host's history and accounting, and it is what stops the slot being handed to
someone else. Retention is therefore tied to erasure *on request*, not to an automatic window an
operator would have to remember to configure.

When a guest exercises a right to erasure, the operator runs one command:

```bash
aethercal-admin guest purge --tenant <slug> --email <guest@example.com>
```

The purge follows one rule — **keep the fact, drop the person** — across *every* place the data
lives, each dealt with by name rather than by walking foreign keys (one table, `webhook_deliveries`,
has no `booking_id` to walk):

- **Redacted, not deleted:** the `bookings` row (name, email, phone, notes, answers, timezone,
  source IP cleared) and the `webhook_deliveries` payload. What remains is *"an appointment happened
  in this slot"* — a fact about the schedule that names nobody.
- **Deleted outright:** everything that exists only in order to *message* the person — queued
  message intents in the `outbox`, the `guest_tokens`, and the `sent_notifications` ledger.
- **Deliberately retained:** money the guest is *owed*. A queued `REFUND` is not a message; deleting
  it would erase the guest **and** keep their money, silently. A queued `EXPIRE_HOLD` is retained for
  the same reason in reverse: dropping it would block the host's calendar for ever. These name nobody
  (their payload is `{provider, provider_ref}`), and a `provider_ref` is retained on exactly the same
  footing a payment record already is — a financial record kept for the obligation it discharges.

The purge is enforced against the live schema by the test suite: any new column that could hold guest
data, any new table that hangs off a booking, and any new queued-effect type all fail the suite until
someone has explicitly classified them as erased or retained. A partial purge that reports success is
the failure mode this design exists to prevent.

## Payments

Payment records (`payments`) and the pseudonymous `provider_ref` that resolves, at the provider, to a
person are kept as financial records for as long as the operator's own tax and chargeback obligations
require. AetherCal never stores raw card data: checkout happens on the provider's hosted page.

## Sub-processors

AetherCal has none of its own. Every external service is one the *operator* connects with their own
credentials and under their own agreement with that provider — typically some of: an SMTP relay
(email), Google Calendar (availability + Meet links), Stripe or Mercado Pago (payments), and an
Evolution/Twilio account (WhatsApp/SMS). If none is configured, that effect is simply skipped and the
booking still succeeds. An operator's published privacy policy should list the ones they actually use.

## For operators

1. Publish a privacy policy that names **you** as the controller and lists the sub-processors you
   configured.
2. Wire your erasure request intake to the `guest purge` command above; it is one-shot and safe to
   re-run.
3. Back up responsibly: a database backup contains guest PII. See the backup/restore runbook in
   [`deploy/README.md`](../deploy/README.md).
