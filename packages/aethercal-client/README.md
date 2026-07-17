# aethercal-client

The Python SDK for the [AetherCal](https://github.com/ernestodota2011/aethercal) API v1 — a thin,
typed [httpx](https://www.python-httpx.org/) client. Responses come back as Pydantic models, not
`dict`s.

```bash
pip install aethercal-client
```

## Book a meeting

```python
import datetime as dt

from aethercal.client import AetherCalClient
from aethercal.schemas.bookings import BookingCreate

with AetherCalClient("http://localhost:8000", api_key="ack_....") as client:
    intro = next(et for et in client.list_event_types() if et.slug == "intro-call")

    today = dt.date.today()
    slots = client.get_slots(
        intro.id,
        window_from=today,
        window_to=today + dt.timedelta(days=7),
        tz="America/New_York",
    )

    booking = client.create_booking(
        BookingCreate(
            event_type_id=intro.id,
            start=slots.slots[0].start,
            guest_name="Jane Doe",
            guest_email="jane@example.com",
            guest_timezone="America/New_York",
        )
    )
    print(booking.id, booking.status)
```

## Three things worth knowing

- **Slot bounds are UTC**; `tz` is only the zone you read them in.
- **Check `slots.availability`.** It is `"ok"` only when the external busy set was known and
  complete for the window. Anything else means a connected calendar was unreachable and AetherCal is
  withholding that host's slots deliberately — an empty list is not the same claim as "no free time".
- **Handle `409`.** Between listing the slots and booking one, somebody else can take it. That is the
  normal race, not an edge case:

```python
from aethercal.client import AetherCalAPIError, AetherCalTransportError

try:
    booking = client.create_booking(payload)
except AetherCalAPIError as exc:      # the API answered — with a non-2xx
    if exc.status_code == 409:
        ...                           # the slot is gone; re-fetch and let the guest choose again
except AetherCalTransportError:       # no response at all: DNS, refused, TLS, timeout
    ...
```

Both descend from `AetherCalError`, so a single `except` catches everything the SDK raises.

## The async client

`AsyncAetherCalClient` currently implements only `health()` and `ping()`. The resource methods above
are synchronous-only — call them in a worker thread (`asyncio.to_thread`) from async code until the
async surface catches up.

Full reference: **[docs/sdk.md](https://github.com/ernestodota2011/aethercal/blob/main/docs/sdk.md)**.
A runnable example lives in
[`examples/sdk/`](https://github.com/ernestodota2011/aethercal/tree/main/examples/sdk).

MIT licensed.
