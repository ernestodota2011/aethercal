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
| `e36c28c3` | 2026-07-29 | MEASURED | 239 planned / 200 created, 39 create-leg + 3 reschedule-leg collisions, 40-way races, **all fourteen** controls held. The first run whose §1 **reconciles** with §6 (42 `slot_unavailable` = 39 + 3) and whose confirmation sample is **proven** complete (200 of 200 matched; the mailbox paged to its own reported total instead of to a hardcoded ceiling). Records the same real observability limit as its predecessors: `/metrics/summary` is served by the worker, so a **dead** worker is invisible to the backlog metric (§7). |

> [!warning] ==Three earlier runs were published here as `MEASURED` and have been withdrawn.==
> Their numbers were plausible; what was wrong was the **certificate**, three times over.
>
> `b72197a2` — the first run whose isolation was *derived*, and its controls were sound as far as
> they went. What they did not go to was the two phases that produce §1 and §2. The organic worker
> could lose its own failures (a slots query that FAILED counted as a fully-booked day; any non-2xx
> booking that was not a collision dropped through `if not response.ok: return`), and the
> confirmation sample was taken through a hardcoded `limit=20000` and a silent `if delta_ms >= 0`.
> ==The loss is not hypothetical in this run: its §6 reports **35** `slot_unavailable` responses
> and its §1 reports **32** collisions.== The missing three were reschedule refusals owned by no
> category and reconciled against nothing — visible only by subtracting one section of the report
> from another, which is exactly the arithmetic no reader performs.
>
> ==Two of the traps did NOT fire in it, and saying so matters more than the withdrawal.== Its
> drain-latency sample is 207 for 207 bookings, so the `delta_ms >= 0` filter discarded nothing;
> and it held 273 messages against a ceiling of 20 000. ==Both were live traps that happened not to
> spring, and nothing in the run could have told anyone that.== That is the whole reason they are
> controls now (C13, C14) rather than conditions somebody re-derives per run.
>
> `696a49f6` — the verdict was not bound to the assertions the report presented as proven: the
> double-booking race took no part in it (five winners on one slot would still have read green), a
> control whose prerequisite failed vanished instead of failing, a failed drain invalidated nothing,
> and the latency clock stopped before the response body was read. Re-judged under the current rules
> it returns **VOID**, missing `C10`/`C11`/`C12`.
>
> `dc6cfd8a` — the verdict logic was sound, but two controls still passed **on a broken source**: C3
> read "0 slots" without checking the query had succeeded (a closed Saturday and a dead API are
> indistinguishable that way), and C9's "at most one live appointment" was trivially satisfied by an
> unreadable diary. Its latency also predates the fix that measures reading the body. And it reached
> a throwaway container because the operator aimed it there — not because the code refused anything
> else.
>
> Both were replaced rather than annotated: a withdrawn measurement left in the table is one
> somebody eventually quotes.
