# cal.com webhook contract fixtures (F0-12 / RF-17)

AetherCal replaces the agency's cal.com. Three existing agency services consume cal.com's webhooks;
when cal.com is retired, AetherCal's outgoing webhooks (F1-09) and the compatibility adapter (F1-14)
must keep producing a payload those consumers accept. These fixtures pin that contract.

## What these are
`booking_created.json`, `booking_cancelled.json`, `booking_rescheduled.json` — one payload per
cal.com `triggerEvent`, shaped to the contract enforced by the agency's `cal-com-webhook` service
(`app/models.py`: required `triggerEvent`, `payload.startTime`, `payload.organizer{id,name,email}`,
`payload.attendees[>=1]{email,name}`, `payload.eventTypeId`; unknown fields ignored) plus the fields
in cal.com's public webhook docs (endTime, uid, bookingId, status, cancellationReason, videoCallData,
and the reschedule* fields).

## Provenance & data
The **structure and field formats** were verified against real cal.com bookings (v2 API, 2026-07-09)
and against the agency consumer's Pydantic model. The **values are entirely synthetic** — no real
attendee names/emails/phones, uids, or event-type ids — because this is a public repository.

> [!] Before the F1 cutover (cancelling cal.com), capture at least one **real** webhook delivery per
> trigger (e.g. a webhook.site subscription + a live booking -> cancel -> reschedule) and diff it
> against these fixtures, in case cal.com adds a field a consumer silently depends on. That real
> capture is the remaining half of F0-12.

Contract validator: `aethercal.server.integrations.calcom.contract.validate_calcom_webhook`.
