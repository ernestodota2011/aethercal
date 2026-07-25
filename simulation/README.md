# Two-week load simulation

A pilot's rubric asks for "two weeks with green metrics, **measured**". Two weeks of calendar time
cannot be hurried — but two weeks of *volume*, and the concurrency real traffic never produces, can
be generated in minutes. This directory does that against a **throwaway** stack, and reports the
numbers together with the controls that make them believable.

```bash
simulation/scripts/run.sh              # up → simulate → report → down
simulation/scripts/run.sh --keep       # leave the stack up to inspect afterwards
```

A run writes `simulation-report.md` (for a person) and `simulation-report.json` (for diffing one run
against the next).

## The rule this harness lives by

> A number without a control is an anecdote.

Every claim the report makes has a case that **must fail**, and a control that comes back green
**voids the run** rather than improving it. The harness exits non-zero unless every control held, so
a run that proved nothing can never read as a green one.

==And the verdict is bound to the REQUIRED SET of control ids, not to however many happened to be
appended.== That distinction is the whole point, and it was learned the hard way: the first version
of this harness appended each control inside the `if` that created its subject, so a missing
prerequisite produced *no control* rather than a failing one — and "7 of 7 held" reads exactly as
well as "12 of 12 held". A pass count derived from the list it summarises **cannot see its own
omissions**. So `report.REQUIRED_CONTROL_IDS` names them independently, an id that never appears is
`VOID` (not merely `INCOMPLETE`), and a prerequisite that cannot be met emits `Control.not_run(...)`
— a fact — instead of silence.

The same version stamped `MEASURED` while the **double-booking race itself took no part in the
verdict**: it lived in a table and nowhere else, so a run with five winners on one slot would have
been certified green. `C10` exists because the headline claim of a document must be able to
invalidate that document.

| id | What it guards | It passes only if |
|---|---|---|
| **C1** | the no-double-booking claim | booking an already-taken slot is refused `409 slot_unavailable` |
| **C2** | ==the race oracle itself== | the same N-way burst at N *distinct* slots yields **N winners** |
| **C3** | the schedule is enforced | a closed Saturday offers **zero** slots |
| **C4** | the daily cap (`max_per_day=2`) | the third booking of a day is refused or no longer offered |
| **C5** | ==the backlog metric is live== | work stranded by a stopped worker is **seen** on restart, then cleared |
| **C6** | the no-show guard | a no-show on an appointment that has not ended is refused `409 not_ended` |
| **C7** | cancel idempotency | N simultaneous cancels emit **exactly one** `booking.cancelled` webhook |
| **C8** | a reschedule race leaves ONE successor | exactly one live appointment survives in the lineage |
| **C9** | a cancel racing a reschedule | **never** two live appointments (either order is legitimate) |
| **C10** | ==the headline claim itself (RF-04)== | the same-slot race left **exactly one** winner, the rest `slot_unavailable`, nothing unexpected |
| **C11** | the no-show transition | `200` **and** the booking's status really becomes `no_show` |
| **C12** | the drain finished, readably | `due == 0` before the run ends, with zero unexplained scrape failures |

**C2 and C5 audit the harness rather than the product**, and they are the two that matter most.

- Without **C2**, "exactly one winner" is worthless. A harness that had silently serialised its
  threads, or that counted winners wrongly, reports exactly one winner *whatever the product does*.
  C2 runs the identical code path at distinct slots and demands N winners, so a run can tell "the
  product refused 39 requests" apart from "the harness only ever really sent one".
- Without **C5**, every backlog number is unfalsifiable. An instrument wired to a constant zero draws
  exactly the same flat, healthy graph as a queue that is genuinely keeping up. C5 strands real work
  by stopping the worker, then restarts it and demands the metric *see* that work and clear it — the
  product's own documented nightmare ("RLS would have turned the dead-man switch into the corpse")
  turned into a check.

  ==C5 found something while being written, and it is worth stating plainly: `/metrics/summary` is
  served BY the worker.== So while the drain is down the backlog metric does not climb — it is
  **absent** (`Connection refused`). The first version of this control tried to read the backlog
  during the outage and died doing it. The metric therefore detects a drain that is *falling behind*,
  and cannot detect a worker that is *gone*; that needs external liveness monitoring, and the report
  says so rather than letting a reader assume the backlog alarm covers both.

**C7–C9 exist because the winner count is the wrong oracle for a mutation race.** `cancel_booking`
is deliberately **idempotent**, so under a cancel race every contender may legitimately receive
`200`; and in a cancel-versus-reschedule race *both* calls can rightly succeed, because the
reschedule swaps in a successor and the cancel then finds the predecessor already cancelled and is a
no-op. A harness asserting "exactly one 200" would fail a correct product. So the oracles are the
effects instead: exactly one `booking.cancelled` at the sink (C7), and — read back off the diary
itself — exactly one surviving successor after a reschedule race (C8), never two live appointments
after a mixed one (C9).

## Never against a real instance

Three independent things have to go wrong before this can touch something that matters:

1. **`compose.sim.yml` renames the Compose project to `aethercal-sim`.** The stack's first act is
   `down -v`, and under the shipped project name that command would delete a real instance's volume.
   The rename makes the database `aethercal-sim_aethercal-pgdata` — a `down -v` here cannot reach an
   instance named anything else, from any directory, on any host.
2. **The harness refuses to start without `.stack.json`**, which only `stack-up.sh` writes. There is
   no default URL to fall back to, so it cannot be pointed at a live instance by hand.
3. **Every guest address is `@guests.sim.test`**, and every business is created fresh per run.

The intended host is a disposable container that is destroyed afterwards.

## What a run does

1. **Provision** — three businesses across two timezones, each with four event types (see
   `world.py` for why `micro` and `capped` exist) and a webhook pointed at the sink.
2. **Organic load** — a seeded, deterministic two-week plan (`traffic.py`): weekday-weighted volume,
   ES and EN guests, guests in five timezones, cancellations and reschedules. Executed by a pool of
   simultaneous guests, each doing what the booking page does — read the day's offer, then book.
3. **Adversarial concurrency** — `threading.Barrier`-released bursts: N bookings of one slot, N
   cancels of one booking, N reschedules of one booking to N different slots, and a cancel racing a
   reschedule. Two weeks of organic traffic would never collide like this; that is the point.
4. **Controls** — the table above.
5. **Drain** — wait for the outbox to empty, then match each confirmation email to its booking to
   measure booking→confirmation latency across the worker.

## What it measures

Latency (p50/p95/p99/max, **nearest-rank**, measured client-side) for the slots read, booking
create, cancel, reschedule, each race, and booking→confirmation-email. Peak outbox backlog and peak
oldest-due age. Winners and refusals per race, by machine error code. Every outcome in one taxonomy,
successes included — because "40 failures" means nothing without "out of how many".

## What it cannot tell you

The report **generates** its own limits section, so it always describes the run that actually
happened rather than a caveat somebody wrote once and forgot. The two that matter most:

- ==**It cannot produce "≥5 bookings from real humans."**== That is a fact about **adoption**. Every
  booking here is synthetic, against a throwaway database. No volume of load substitutes for it, and
  the pilot still has to meet that criterion on its own.
- ==**It compresses two weeks of VOLUME, not two weeks of TIME.**== Nothing here sees certificate
  expiry, log rotation, a drifting cron, a slow memory leak, connection churn over days, a 24-hour
  reminder firing, or a DST transition. Duration-dependent failure is what a soak test finds, and
  this is not a soak test.

Also out of scope by construction: real email deliverability (mail lands in Mailpit over plaintext
SMTP on the same host), real network conditions (no reverse proxy, no TLS, no WAN), the booking
**page** (that is `e2e/` and the CWV guard), external calendar integrations, payments, and anything
about running more than one worker.

## Layout

| Path | What |
|---|---|
| `compose.sim.yml` | Third overlay on the shipping stack: the renamed project, the operator token, a bigger mailbox |
| `scripts/stack-up.sh` | Boots the throwaway stack, creates the businesses, writes `.stack.json` |
| `scripts/run.sh` | The one command: up → simulate → report → down |
| `aethercal_sim/client.py` | Stdlib HTTP and the stopwatch. Never raises on a status — a refusal is data |
| `aethercal_sim/world.py` | `.stack.json`, and the businesses and event types a run is built on |
| `aethercal_sim/traffic.py` | The seeded two-week demand plan. Pure, no I/O |
| `aethercal_sim/measure.py` | Percentiles, the error taxonomy, the backlog sampler, the mailbox |
| `aethercal_sim/scenarios.py` | The three phases and every control |
| `aethercal_sim/report.py` | The Markdown report, its verdict, and the generated limits |
| `tests/` | The harness's own calibration — percentile arithmetic, plan determinism, the verdict |

`tests/` runs in the ordinary suite (`pytest`): it is offline and opens no socket, so it costs
nothing and cannot trip the network guard. The parts that need a running stack are not pytest tests
at all — they are the harness, run by `scripts/run.sh`.

## Relationship to the other test tackle

- `e2e/` proves **one** guest's journey is correct across all three surfaces, in a real browser.
- `loadtest/slots.js` measures the **read** path with k6 at 50 VUs.
- `simulation/` is the **write** path at volume, under deliberate contention, with the outbox
  actually draining — the scenario `loadtest/README.md` says should get "its own file, its own
  throwaway tenant, and a teardown".
