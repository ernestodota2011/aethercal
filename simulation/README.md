# Two-week load simulation

A pilot's rubric asks for "two weeks with green metrics, **measured**". Two weeks of calendar time
cannot be hurried — but two weeks of *volume*, and the concurrency real traffic never produces, can
be generated in minutes. This directory does that against a **throwaway** stack, and reports the
numbers together with the controls that make them believable.

```bash
simulation/scripts/run.sh              # up → simulate → report → down
simulation/scripts/run.sh --keep       # leave the stack up to inspect afterwards
simulation/scripts/stack-down.sh       # the exit for a --keep run: down -v AND restore deploy/.env
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
| **C13** | ==§1 itself: the organic phase is fully explained== | every planned intent lands in a known outcome, the outcomes **sum to the plan**, and no request failed unexpectedly |
| **C14** | ==§2's slowest row: the confirmation sample is whole== | the mailbox was read to its own reported total and **every** created booking matched a message, with no negative deltas |

> [!danger] ==A phase whose failures have nowhere to go will always report a quiet fortnight.==
> **C13** and **C14** exist because §1 and §2 were produced by code that could lose its own
> failures — and both losses moved the numbers in the **flattering** direction, which is exactly why
> nobody was ever going to notice them:
>
> - The organic worker filed a slots query that **failed** as `no_slots_offered`, which §1 prints as
>   *"attempts that met a fully-booked day"*, and dropped any non-2xx booking that was not a
>   collision with a bare `if not response.ok: return`. ==An instance 500-ing its way through the
>   whole run presented as a slightly quieter one, and the verdict still said `MEASURED`.== Now every
>   planned intent ends in exactly one category and the categories must **reconcile against the
>   plan** — the same lesson `REQUIRED_CONTROL_IDS` learned one level up: a count derived from the
>   list it summarises cannot see its own omissions.
> - The mailbox was read with a hardcoded `limit=20000` — equal to the `MP_MAX_MESSAGES` the overlay
>   sets, compared against nothing — and confirmations whose timestamp preceded the booking's
>   reference instant were discarded by a bare `if delta_ms >= 0`. ==A latency sample that loses
>   members reports a FASTER product, and only the fastest confirmations can precede their own
>   POST==, so that filter trimmed precisely the left tail. The report already **carried a warning**
>   that drain latency had *n* samples for *m* bookings — and a run shipped `MEASURED` with it.
>   That is the whole difference between prose and a control.

> [!warning] ==Ask of every control: *would this still pass if the API stopped answering?*==
> If the answer is yes, it is not measuring — it is assuming. Two of these failed that question:
> **C3** read "0 slots offered" and nothing else, so a 500, a 401 or a refused connection all
> produced its pass condition (a closed Saturday and a dead API look identical); and **C9** asks
> "at most one live appointment?", which an unreadable diary satisfies trivially with an empty list.
> Both now require a well-formed 2xx first and **fail, naming what broke**, on anything else. C4's
> third probe had the same shape and was hardened with them.
>
> ==C3's version of that judgement was INLINED, so the organic phase never inherited it.== It now
> lives in one function (`read_offer`) that both call, because a rule enforced at one of its call
> sites is a rule with a hole in it — and that hole was §1's biggest number.

> [!warning] ==And the mirror question: *could this FAIL while the product is perfect?*==
> A control that can go red by luck is not a control either; worse, its failure accuses the product
> of a fault belonging to the harness. Two failed this one, and both were fixed by changing the
> ORACLE rather than the sequence:
>
> - **C7** slept a fixed 12 seconds and read the sink once. A webhook arriving at 13 seconds read as
>   `0`, which is ==indistinguishable from the duplication C7 exists to detect== — a timeout
>   reported as a defect, lying in both directions at once. It now drains the outbox first (so every
>   queued delivery has been *attempted*), polls to an explicit deadline, then keeps watching
>   through a settling window so a late duplicate is still caught — and the report distinguishes
>   *"nothing arrived within N s"* from *"more than one arrived"*.
> - **C5** required `first_reading.due > baseline`, where `due` is an **instantaneous gauge** and
>   the restarted worker both serves that metric and drains the queue. On a fast tick it finishes
>   draining before the first scrape is answered, and the control failed a system that behaved
>   perfectly. It now judges on **durable** signals that cannot be missed by arriving late: the
>   DB-derived row count grew by at least the work stranded, and the restarted worker's
>   `drain.delivered` counter (zero at boot, monotonic after) accounts for it. Catching the gauge
>   mid-climb is still recorded as corroboration — it is simply no longer decided by a coin toss
>   against the system under test.

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

## Never against a real instance — enforced, not promised

==This section used to describe a convention. It now describes a check.== The first version took
`--stack-file` as an arbitrary path, so nothing structural stopped the harness being aimed at a live
instance and purging it; the guarantee lived in this README and in whatever the operator had been
told — the two places that do not execute. That is the same defect the rest of this directory exists
to hunt (*the datum that decides is not produced by whoever asserts it*), so it had no business
being the one thing taken on faith.

`assert_disposable_stack` now runs **before anything is created, purged or modified**, and raises if
any of these fail:

1. **The Compose project must be `aethercal-sim`.** The stack opens with `down -v`; under the
   shipped project name that would delete a real instance's volume. The rename makes the database
   `aethercal-sim_aethercal-pgdata`, unreachable from any other project, on any host.
2. **Every endpoint must be loopback.** A hostname or a LAN address means something else is being
   addressed.
3. **The stack file must carry a 128-bit nonce**, generated by `stack-up.sh` for this stack.
4. ==**The database must carry that same nonce.**== `stack-up.sh` plants a schedule named
   `sim-marker-<nonce>` in the first business, and the harness reads it back through the API before
   doing anything else. **This is the check a hand-edited `.stack.json` cannot pass** — only the
   script that created the database could have planted it, and only for this run. A *stale
   simulation stack* left from an earlier run is refused too, because the nonce is fresh every time.

There is no `--stack-file` flag any more: the path is fixed to the one file `stack-up.sh` writes.
Every guest address is `@guests.sim.test`, and every business is created fresh per run.

The intended host is a disposable container that is destroyed afterwards — but the checks above hold
wherever it is pointed.

### `deploy/.env` is borrowed, not taken

The shipping stack reads `deploy/.env` (`env_file:` on each service), so `stack-up.sh` has to write
it. It **backs the existing file up first**, and `run.sh` restores it from a `trap` on the way out —
on success, on failure, and on Ctrl-C. A boot that dies halfway no longer leaves shared repo
configuration replaced by test-only values.

> [!danger] ==`--keep` has no "way out", and that turned the backup into a one-shot.==
> A `--keep` run deliberately leaves the stack up **and** the backup in place, because the stack
> that is still running goes on reading the test-only `deploy/.env`. Start `stack-up.sh` again in
> that state and its `cp` copied the **simulation's** env over the saved original — destroying the
> only copy of the developer's file, silently, inside the step whose entire purpose is to preserve
> it. The two events are far enough apart in time that nobody connects them.
>
> So a leftover `${ENV_BACKUP}.state` is now a **hard stop**: `stack-up.sh` refuses to start and
> names the way out, and `scripts/stack-down.sh` is that way out — it tears the throwaway stack down
> **and** restores `deploy/.env`, which a bare `docker compose down -v` never did.

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
| `scripts/stack-down.sh` | Tears it down **and gives `deploy/.env` back** — the exit a `--keep` run otherwise has none of |
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
