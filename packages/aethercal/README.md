# aethercal

The umbrella package for [AetherCal](https://github.com/ernestodota2011/aethercal) — open-source
calendar and appointment-scheduling infrastructure.

```bash
pip install aethercal
```

One line gets you all three libraries:

| Package | What it is |
|---|---|
| [`aethercal-core`](https://pypi.org/project/aethercal-core/) | The scheduling engine — RFC 5545 recurrence, timezone-correct availability, slots. Pure, zero I/O. |
| [`aethercal-schemas`](https://pypi.org/project/aethercal-schemas/) | The API v1 contract, as Pydantic models. |
| [`aethercal-client`](https://pypi.org/project/aethercal-client/) | The HTTP SDK (sync + async). |

Want only one of them? Install it directly — none of them depends on this package.

## What this package is *not*

It is **not the server**. AetherCal's API, booking page and admin ship as the self-host container
built from the repository — `docker compose up`, one database, two variables. See the
**[quickstart](https://github.com/ernestodota2011/aethercal/blob/main/docs/quickstart.md)**.

The calendar component is separate too:
[`aethercal-ui`](https://pypi.org/project/aethercal-ui/) for Reflex, and
[`@aethercal/calendar-react`](https://www.npmjs.com/package/@aethercal/calendar-react) for React.

MIT licensed.
