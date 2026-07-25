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
| `696a49f6` | 2026-07-25 | MEASURED | First run of the harness. 239 planned / 199 created, 40 organic collisions, 40-way races, all nine controls held. Recorded a real observability limit: `/metrics/summary` is served by the worker, so a **dead** worker is invisible to the backlog metric (§7). |
