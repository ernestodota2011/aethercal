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

> **Self-hosting, with your webhook target on your own network?** A URL like
> `http://n8n:5678/webhook/...` or `http://192.168.1.50:5678/...` — anything not reachable from the
> public internet — is **refused by default**. Read
> [Delivering to a private network](#delivering-to-a-private-network). It is one environment
> variable, and you should understand what it opens before you set it.

## Delivering to a private network

By default AetherCal POSTs only to a **globally routable** address. A target that resolves to a
private one — `192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`, a Docker network, a VPN, `127.0.0.1` — is
refused: the delivery is parked `dead` and never retried.

That default is deliberate, and for a hosted instance it is the right one. It is also the wrong one
for the deployment most people run this in: **your n8n, your CRM, your ERP are on the same network as
AetherCal.** So you can declare which private networks this instance may reach:

```bash
# Explicit CIDRs, comma-separated. Unset = none, and none is the default.
AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS=192.168.1.0/24,172.17.0.0/16
```

A subscription pointing at `http://192.168.1.50:5678/webhook/aethercal` is now delivered, signed
exactly like any other. Everything **outside** those CIDRs stays refused.

### The risk, stated plainly

**A webhook URL is chosen by whoever creates the subscription. This variable decides where those URLs
are allowed to reach.**

That is the whole of it. If someone who can create a subscription points it at
`http://192.168.1.10:9200/` and you have declared `192.168.1.0/24`, AetherCal will POST a signed
booking envelope at your Elasticsearch — and the delivery log will tell them whether it answered.
This is **Server-Side Request Forgery**: the server is used as a proxy into a network the caller
cannot otherwise reach. Services on an internal network are routinely unauthenticated *because* they
are on an internal network, and "it is behind the firewall" is precisely the assumption being spent
here.

So:

- **declare the narrowest network that contains your target.** `192.168.1.0/24` if that is where n8n
  lives — not `192.168.0.0/16`, and not "all of RFC1918 while I am at it";
- on a **single-tenant** instance, where you are the only person who can create subscriptions, the
  exposure is small: you are pointing your own server at your own service;
- on an instance where **anyone else** can create subscriptions, every address inside that CIDR is
  something they can make your server talk to. Decide accordingly.

### Why it is a list and not a switch

There is no `allow_private = true`. A boolean is something people copy out of a forum post without
reading the sentence after it; a CIDR is a statement about a specific network you had to go and look
up. The mechanism is the same — the difference is whether you knew what you were opening.

### What cannot be allowlisted, whatever you write

The process **refuses to start** if you declare any of these:

| Refused | Why |
|---|---|
| `0.0.0.0/0`, `::/0` | The default route holds loopback, the cloud-metadata address and every private range at once. This is the single entry that would turn the feature into the vulnerability it exists to avoid. |
| `169.254.0.0/16`, `fe80::/10` | Link-local. `169.254.169.254` is the cloud metadata endpoint — on AWS/GCP/Azure, your instance's credentials one GET away. No webhook consumer has ever lived there. |
| Any public CIDR | Public targets already work. They need no allowlist. |
| Multicast; host bits set (`192.168.1.5/24`); a bare address with no prefix (`10.0.0.0`) | Typos. `10.0.0.0` parses as `10.0.0.0/32` — one address — which is almost never what was meant, and would leave you with an allowlist that permits nothing while looking configured. |

Declarable: subnets of `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10` (CGNAT —
where Tailscale lives), `127.0.0.0/8`, `fc00::/7` (IPv6 ULA), `::1/128`.

**Loopback (`127.0.0.0/8`) is allowed, and warns at boot.** On a bare-metal box running AetherCal and
n8n side by side it is the correct target. It is also the widest thing you can open: every service
bound to localhost *because it treats localhost as trusted* becomes reachable from a caller-supplied
URL. Narrow it if you can.

### DNS rebinding is still refused

Declaring `192.168.1.0/24` does not make `192.168.1.50` reachable *by any name that can be pointed at
it*. AetherCal resolves the target, validates the addresses, and then connects to **that exact IP** —
it never re-resolves. A hostname that answers with a public address for the check and a private one
for the socket is refused (`blocked-dns-rebind`) **even when the private address is inside your
CIDR**: you declared a network, not a licence for any hostname on the internet to be re-pointed into
it mid-flight. TLS is unaffected — SNI and certificate verification stay bound to the real hostname.

### A refused delivery says so

It is parked `dead` with a reason on the row (`webhook_deliveries.error_reason`), a `WARNING` in the
log, and a series on `GET /metrics`:

| Reason | Meaning |
|---|---|
| `blocked-private-target` | Not routable, and not inside any CIDR you declared. **If this is your own n8n, the variable above is the fix.** |
| `blocked-dns-rebind` | The address changed between validation and connect. |
| `dns-failure` | The name did not resolve. **Retried** — not fatal. |
| `transport-error` | Connection refused / TLS / timeout. Retried. |
| `http-error` | Your endpoint answered, but not with a 2xx. Retried. |

```bash
# Is this instance refusing to send anywhere?
curl -sH "Authorization: Bearer $AETHERCAL_METRICS_TOKEN" localhost:8000/metrics \
  | grep aethercal_webhook_deliveries_failed

docker compose logs app | grep blocked-private-target
```

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
