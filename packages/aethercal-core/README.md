# aethercal-core

The AetherCal scheduling engine: RFC 5545 recurrence, timezone-correct availability, slot
computation and conflict detection.

**Pure.** It imports no other AetherCal package and performs **no I/O** — no database, no network,
no clock (the current time is passed in). Import contracts enforce that in CI. It is what lets the
engine be used as a library on its own, and what lets it be property-tested against DST edges and an
independent oracle.

```bash
pip install aethercal-core
```

## Recurrence that survives a DST change

An event's `dtstart` is **naive wall-time** and its `timezone` travels beside it — because "every
Friday at 09:00" means 09:00 on the clock, not a fixed offset from UTC. Expanding a series across a
DST boundary shows why that matters:

```python
import datetime as dt
from zoneinfo import ZoneInfo

from aethercal.core.model.event import Event
from aethercal.core.model.interval import TimeInterval
from aethercal.core.recurrence import expand

NY = ZoneInfo("America/New_York")

event = Event(
    dtstart=dt.datetime(2026, 3, 6, 9, 0),       # naive wall-time
    duration=dt.timedelta(minutes=30),
    timezone="America/New_York",                  # ...the zone is stored separately
    rrule="FREQ=WEEKLY;COUNT=3",
)
window = TimeInterval(
    start=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
    end=dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
)

for occurrence in expand(event, window):
    utc = occurrence.interval.start.astimezone(dt.UTC)
    local = occurrence.interval.start.astimezone(NY)
    print(utc.strftime("%Y-%m-%d %H:%MZ"), " local:", local.strftime("%H:%M %Z"))
```

```
2026-03-06 14:00Z   local: 09:00 EST
2026-03-13 13:00Z   local: 09:00 EDT
2026-03-20 13:00Z   local: 09:00 EDT
```

The **UTC instant moves** when the spring-forward lands mid-series; the **local time does not**. An
engine that stores an offset instead of a zone gets the second and third occurrences an hour wrong.

## What is in it

| Module | What it does |
|---|---|
| `aethercal.core.recurrence` | `expand(event, window)` — RFC 5545 `RRULE` / `EXDATE` / `RDATE` into concrete occurrences |
| `aethercal.core.availability` | `available_intervals(...)` — a weekly schedule plus date overrides into real intervals |
| `aethercal.core.slots` | `available_slots(available, busy, event_type, now)` — bookable slots, applying duration, increment, buffers, minimum notice and maximum advance |
| `aethercal.core.conflicts` | `has_conflict(...)`, `validate_no_conflict(...)`, `find_overlapping_pairs(...)` |
| `aethercal.core.ical` | `event_to_ics(...)` / `event_from_ics(...)` |
| `aethercal.core.model` | The domain types: `Event`, `Booking`, `EventType`, `Schedule`, `TimeInterval`, `Occurrence`, … |

## The rest of AetherCal

This is the engine alone. The API, the booking page, the SDK and the calendar component live in the
same [repository](https://github.com/ernestodota2011/aethercal).

MIT licensed.
