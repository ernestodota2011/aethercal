# aethercal-schemas

The [AetherCal](https://github.com/ernestodota2011/aethercal) API v1 contract, as Pydantic models —
the request and response types the server emits and the SDK parses.

```bash
pip install aethercal-schemas
```

Install it directly when you are building your own client, or validating AetherCal payloads on the
receiving end. If you are calling the API from Python, install
[`aethercal-client`](https://pypi.org/project/aethercal-client/) instead — it depends on this one and
hands you the parsed models already.

```python
from aethercal.schemas.bookings import BookingCreate, BookingRead
from aethercal.schemas.event_types import EventTypeCreate, EventTypeRead
from aethercal.schemas.schedules import ScheduleCreate, TimeRangeSchema
from aethercal.schemas.slots import SlotsResponse
from aethercal.schemas.webhooks import WebhookEnvelope, WebhookEventName
```

| Module | Covers |
|---|---|
| `event_types` | Bookable meetings: duration, host, schedule, buffers, notice, translations, questions |
| `schedules` | Weekly availability (`0` = Monday … `6` = Sunday) and date overrides |
| `slots` | The computed slots response, including the `availability` flag |
| `bookings` | Create, read, reschedule; `BookingStatus` |
| `webhooks` | Subscriptions and the signed `WebhookEnvelope` |

The domain types these build on (`TimeInterval`, `Event`, `BookingStatus`, …) come from
[`aethercal-core`](https://pypi.org/project/aethercal-core/), the pure engine.

MIT licensed.
