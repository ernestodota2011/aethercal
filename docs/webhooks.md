# Webhooks

AetherCal POSTs a signed JSON envelope to your endpoint when a booking changes.

**Read the [delivery contract](#delivery-is-at-least-once) before you write a handler.** Delivery is
**at-least-once**: a handler written as though it were exactly-once will eventually double-charge
somebody.

## Subscribe

```bash
curl -X POST "$AETHERCAL_URL/webhooks" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "url": "https://example.com/hooks/aethercal",
        "events": ["booking.created", "booking.cancelled", "booking.rescheduled"]
      }'
```

The response carries a `secret` — **returned exactly once, at creation**. It is stored encrypted and
no later read returns it. Lose it and you must rotate the subscription.

Only `http` and `https` URLs are accepted, and the URL is re-checked against an SSRF egress guard
immediately before every POST, so a subscription cannot be turned into a probe of private addresses
inside the host's network.

## Events

| Event | Fires when |
|---|---|
| `booking.created` | A guest books a slot |
| `booking.cancelled` | A booking is cancelled |
| `booking.rescheduled` | A booking is moved |

A reschedule does not mutate the booking — it **creates a new one** that inherits the calendar
identity (`ical_uid`) and cancels the predecessor. The payload's `rescheduled_from_id` points back
at the booking that was replaced.

## The envelope

```json
{
  "event": "booking.created",
  "api_version": "1",
  "timestamp": "2026-07-13T13:00:00+00:00",
  "data": {
    "id": "5a13f24c-8e79-4240-b661-b8d4846fe01a",
    "tenant_id": "035683a9-9532-44be-90c1-73f7398d4492",
    "event_type_id": "76d161bd-117d-41d6-b3a5-e665713e0a5e",
    "status": "confirmed",
    "start": "2026-07-13T13:00:00+00:00",
    "end": "2026-07-13T13:30:00+00:00",
    "guest_name": "Jane Doe",
    "guest_email": "jane@example.com",
    "guest_timezone": "America/New_York",
    "answers": {},
    "meeting_url": null,
    "rescheduled_from_id": null
  }
}
```

`start` and `end` are UTC. `api_version` is bumped only on a breaking change to this contract.

## Verify the signature

Every delivery carries an HMAC-SHA256 of the **exact bytes posted**, keyed by your subscription
secret:

```
X-AetherCal-Signature: sha256=<hex>
```

Verify against the **raw request body**. Do not re-serialize the parsed JSON: AetherCal signs a
canonical form (sorted keys, compact separators) and your framework's re-serialization will not
reproduce those bytes.

```python
import hashlib
import hmac

def verify(raw_body: bytes, header: str, secret: bytes) -> bool:
    presented = header.removeprefix("sha256=")
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(presented, expected)   # constant-time; never ==
```

Reject anything that fails. An unsigned or wrongly signed POST did not come from AetherCal.

## Delivery is at-least-once

> **A crash between "your endpoint accepted" and "we recorded that it accepted" re-sends the
> event.** You *will* receive duplicates. That is the contract, not a bug.

AetherCal writes the intent to deliver in the *same database transaction* as the booking itself, and
a worker drains that queue afterwards. That is what makes an event impossible to **lose** — and it
is the same thing that makes a duplicate possible: the window between your `200` and our commit of
that fact is narrow, but it is real. A process killed inside it retries an effect that already
happened.

Closing the window completely would require provider-side idempotency at our end. Until that exists
the residual is accepted deliberately — **a duplicate notification is safer than a missing one** —
and the last mile is yours:

**Make your handler idempotent.** Key on the booking `id` together with the `event`, remember what
you have already processed, and make a repeat a no-op:

```python
key = (payload["data"]["id"], payload["event"])
if already_processed(key):        # this exact effect has been seen
    return 200                    # ack again; do nothing
process(payload)
mark_processed(key)
return 200
```

Do not key on `timestamp`: a retry carries the **same** envelope, so it repeats the original
timestamp rather than the retry's.

### Retries, ordering, and the dead letter

| | |
|---|---|
| Retry schedule | Exponential backoff, `30s × 2^(attempts-1)`, capped at **1 hour** |
| Attempts | **6** — after that the delivery is parked `dead` and is **not retried automatically** |
| Success | Any 2xx |
| Ordering | **Not guaranteed.** A retried `booking.created` can land *after* the `booking.cancelled` that followed it. Drive your state from the payload, never from arrival order. |

A `dead` delivery is visible in the logs and needs a human to replay it. So do not treat "we sent
it" as "they received it": if your integration must not silently drift, reconcile periodically
against `GET /api/v1/bookings/`.

**Answer fast.** The endpoint is called from the delivery worker; queue the real work on your side
and return `200` immediately. A slow handler becomes a timeout, and a timeout becomes a retry — that
is, another duplicate.
