# Python SDK — `aethercal-client`

A thin, typed [httpx](https://www.python-httpx.org/) client for the AetherCal API v1. Responses are
parsed into the Pydantic models from `aethercal-schemas`, so what you get back is typed, not a
`dict`.

```bash
pip install aethercal-client
```

## Connect

```python
from aethercal.client import AetherCalClient

with AetherCalClient("http://localhost:8000", api_key="ack_....") as client:
    print(client.health())      # {'status': 'ok'}
    print(client.ping())        # True
```

The client is a context manager — use it that way and it closes its connection pool for you. The
`api_key` becomes an `Authorization: Bearer` header; you can omit it only for `health()`, the one
unauthenticated route.

| Argument | Default | Meaning |
|---|---|---|
| `base_url` | — | The server root, e.g. `https://cal.example.com`. **Not** the `/api/v1` prefix — the client adds it. |
| `api_key` | `None` | Your API key. |
| `timeout` | `10.0` | Seconds, applied to connect/read/write/pool. |
| `transport` | `None` | An `httpx` transport, for tests. |

## The booking flow

The SDK covers the **guest-facing** flow: discover what is bookable, find a free slot, book it,
then cancel or reschedule it.

```python
import datetime as dt

from aethercal.client import AetherCalClient
from aethercal.schemas.bookings import BookingCreate

with AetherCalClient("http://localhost:8000", api_key="ack_....") as client:
    # 1. What can be booked?
    event_types = client.list_event_types()
    intro = next(et for et in event_types if et.slug == "intro-call")

    # 2. When is it free? Slot bounds come back in UTC; `tz` is the display zone.
    today = dt.date.today()
    slots = client.get_slots(
        intro.id,
        window_from=today,
        window_to=today + dt.timedelta(days=7),
        tz="America/New_York",
    )
    if slots.availability != "ok":
        raise SystemExit("a connected calendar was unreachable — no slots are offered")

    # 3. Book the first one.
    booking = client.create_booking(
        BookingCreate(
            event_type_id=intro.id,
            start=slots.slots[0].start,
            guest_name="Jane Doe",
            guest_email="jane@example.com",
            guest_timezone="America/New_York",
        )
    )
    print(booking.id, booking.status)      # ... BookingStatus.CONFIRMED
```

A runnable version of exactly this is in [`examples/sdk/`](../examples/sdk/).

### Cancelling and rescheduling need a guest token

`cancel_booking` and `reschedule_booking` take a **signed guest token** — the one AetherCal puts in
the links of the guest's confirmation email. It authorizes *that guest* to act on *that booking*, so
holding the API key is not enough to cancel a stranger's booking by guessing its id.

```python
booking = client.cancel_booking(booking_id, token="<signed guest token>")

booking = client.reschedule_booking(
    booking_id,
    new_start=dt.datetime(2026, 7, 20, 14, 0, tzinfo=dt.UTC),
    token="<signed guest token>",
)
```

Rescheduling does not mutate the booking: it creates a **new** one that inherits the calendar
identity (`ical_uid`) and marks the old one cancelled. What comes back is the successor — new `id`,
with `rescheduled_from_id` pointing at the predecessor.

## Method reference

### `AetherCalClient` — synchronous

| Method | Calls | Returns |
|---|---|---|
| `health()` | `GET /api/v1/health` | `dict` |
| `ping()` | `GET /api/v1/health` | `bool` — never raises |
| `get_branding()` | `GET /api/v1/branding` | `TenantBrandingRead` |
| `list_event_types()` | `GET /api/v1/event-types/` | `list[EventTypeRead]` |
| `get_slots(event_type, *, window_from, window_to, tz)` | `GET /api/v1/slots/` | `SlotsResponse` |
| `create_booking(booking)` | `POST /api/v1/bookings/` | `BookingRead` |
| `cancel_booking(booking_id, *, token)` | `POST /api/v1/bookings/{id}/cancel` | `BookingRead` |
| `reschedule_booking(booking_id, *, new_start, token)` | `POST /api/v1/bookings/{id}/reschedule` | `BookingRead` |

`SlotsResponse` carries `event_type_id`, `timezone`, `slots` (each a `start`/`end` pair, **in UTC**)
and `availability` — which is `"ok"` only when the external busy set was known and complete for the
whole window. Anything else means a connected calendar could not be established, and AetherCal
withholds that host's slots rather than risk a double-booking. Check it before you offer a time.

`TenantBrandingRead` carries `display_name`, `logo_url`, `accent_color` and `timezone` — the four
things a business's own booking page shows. `get_branding()` takes **no argument**: the business is
the one your API key belongs to, resolved server-side, so there is nothing to pass and nothing that
could point at another business's brand. `display_name` arrives already resolved (the business's
public name, or its registered name when it has not set one), and `timezone` is the zone to show
times in before a visitor picks their own.

### `AsyncAetherCalClient` — asynchronous

> **Today the async client implements only `health()` and `ping()`.** The resource methods above are
> synchronous-only. If you need the booking flow inside an async application, call the sync client in
> a worker thread (`asyncio.to_thread`) until the async surface catches up.

```python
from aethercal.client import AsyncAetherCalClient

async with AsyncAetherCalClient("http://localhost:8000", api_key="ack_....") as client:
    await client.health()
    await client.ping()
```

### Not in the SDK

Creating **event types**, **schedules** and **webhooks** is an operator task, not a guest one, and
has no SDK method yet. Call those endpoints directly (see the [quickstart](quickstart.md)) or use
the admin.

## Errors

Every error the SDK raises descends from `AetherCalError`, so a single `except` is enough to be
safe.

```python
from aethercal.client import AetherCalAPIError, AetherCalError, AetherCalTransportError

try:
    booking = client.create_booking(payload)
except AetherCalAPIError as exc:          # the API answered — with a non-2xx
    if exc.status_code == 409:
        print("that slot was taken while we were deciding")   # re-fetch the slots
    else:
        print(exc.status_code, exc.error, exc.message)
except AetherCalTransportError:           # no HTTP response at all: DNS, refused, TLS, timeout
    print("the server was unreachable")
```

| Exception | Raised when |
|---|---|
| `AetherCalAPIError` | A non-2xx response. Carries `status_code`, `error` and `message`, parsed from the API's error envelope. |
| `AetherCalTransportError` | The request never produced a response — connection refused, DNS failure, TLS error, timeout. The originating `httpx` exception is chained as `__cause__`. |
| `AetherCalError` | Base class of both. |

**`409 Conflict` is the one you must handle.** Between listing the slots and booking one, somebody
else can take it. That is not an edge case, it is the normal race: refresh the slots and let the
guest choose again.
