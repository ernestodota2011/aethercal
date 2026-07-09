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

## F3 — Multichannel workflows

Configurable reminders over email, WhatsApp, and SMS; no-show tracking; a durable task runner.

## F4 — Payments + multi-business + timeline

Deposits and payment providers; light multi-tenant management; the resource/timeline view.

## F5+ — Horizon

Round-robin / collective / group bookings, Outlook/CalDAV, mobile interactions, embeds/widget.
