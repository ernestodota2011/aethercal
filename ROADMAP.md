# AetherCal Roadmap

Phased delivery; each phase ships something used in production before the next begins.

## F0 — Foundations (in progress)

The pure scheduling engine: RFC 5545 recurrence, timezone correctness, availability, and slot
computation — property-tested (Hypothesis) against DST edges and an independent oracle. Plus
de-risking spikes (interactive calendar component; Google Calendar) and public CI.

## F1 — MVP: "Goodbye cal.com"

Public bilingual booking page, Google busy-check, transactional email with `.ics`, an
authenticated API, signed outgoing webhooks v1, a minimal admin, and Docker deploy. Shadow run,
then cutover.

## F2 — Calendar UI v1 + public launch

`aethercal-ui`: month/week/day/list views with drag-and-drop and optimistic reconciliation,
theming, and internationalization. Public demo and announcement.

## F3 — Multichannel workflows — SHIPPED

Configurable reminders over email, WhatsApp, and SMS; no-show tracking; a durable task runner
(a transactional outbox: the intent commits with the booking, and the drain is idempotent).

## F4 — Payments + multi-business + timeline — SHIPPED

Deposits and payment providers; multi-business isolation; the resource/timeline view.

Each business is sealed from the others by PostgreSQL row-level security over three database
roles, holds its own encrypted provider credentials, and sends from its own account — never the
instance operator's. Payments run in **provider test mode only**: the Stripe adapter has never
been exercised against a live key, and the Mercado Pago adapter has never run against a real or
sandbox account. Partial refunds are not modelled — see F5.

## F5+ — Horizon

Round-robin / collective / group bookings, Outlook/CalDAV, mobile interactions, embeds/widget.
