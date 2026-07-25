# Committed runs

One file per run worth keeping, named `YYYY-MM-DD-run-<id>.md` with its `.json` twin. The default
output paths at `simulation/` are gitignored precisely so that committing a report is a **decision**
rather than a side effect of having run one.

Reports accumulate here so a p95 can be compared with the last release instead of admired on its
own. The JSON exists for exactly that: the same numbers in a shape you can diff or plot. The plan is
seeded, so two runs at the same seed carry the same load and any movement is the product's, not the
harness's.

==Read §5 (controls) before §2 (latency).== A run whose verdict is not `MEASURED` proves nothing at
all, however good its numbers look — which is why the verdict is the first line of every file.

| Run | Date | Verdict | Notes |
|---|---|---|---|
| `dc6cfd8a` | 2026-07-25 | MEASURED | 239 planned / 204 created, 35 organic collisions, 40-way races, **all twelve** controls held. Recorded a real observability limit: `/metrics/summary` is served by the worker, so a **dead** worker is invisible to the backlog metric (§7). |

> [!warning] ==A previous run, `696a49f6`, was published here as `MEASURED` and has been withdrawn.==
> Its numbers were plausible; its **certificate** was not. The verdict was not bound to the
> assertions the report presented as proven: the double-booking race took no part in it (a run with
> five winners on one slot would still have read green), a control whose prerequisite failed
> vanished instead of failing, a failed drain invalidated nothing, and the latency clock stopped
> before the response body was read.
>
> Re-judged under the current rules that run returns **VOID**, missing `C10`, `C11` and `C12`. It
> was replaced rather than annotated, because a withdrawn measurement left in the table is one
> somebody eventually quotes.
