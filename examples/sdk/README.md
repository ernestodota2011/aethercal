# SDK example — book a meeting

The guest booking flow end to end with `aethercal-client`: list what is bookable, find a free slot,
book it, and prove the slot cannot be taken twice.

## Run it

You need a running AetherCal with an event type. The [quickstart](../../docs/quickstart.md) gets you
there — it creates one with the slug `intro-call`.

```bash
pip install aethercal-client

export AETHERCAL_URL="http://localhost:8000"
export AETHERCAL_KEY="ack_...."     # aethercal-admin issue-api-key --tenant-slug demo --name sdk

python book_a_meeting.py            # or: python book_a_meeting.py my-event-slug
```

```
Event type: Intro call (30 min)
175 free slots — taking the first one.
Booked aafa6b91-15c5-4d28-aafc-492fce9fab53 — confirmed — 2026-07-13 13:30:00 to 2026-07-13 14:00:00
Re-booking the same slot was refused with 409, as it should be.
```

It books a **real** appointment. Point it at a throwaway tenant, not your live calendar.

## What it demonstrates

- **Slot bounds are UTC.** `tz` is only the zone you want to read them in.
- **Check `availability` before offering a time.** It is `"ok"` only when the external busy set was
  known and complete. Anything else means a connected calendar was unreachable, and AetherCal
  withholds that host's slots on purpose — an empty list is not the same claim as "no free time".
- **`409` is the normal race, not an edge case.** Between listing slots and booking one, somebody
  else can take it. The example forces that conflict and handles it. So should you.

The full method reference is in the [SDK guide](../../docs/sdk.md).
